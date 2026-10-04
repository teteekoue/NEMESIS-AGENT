#!/usr/bin/env python3
"""Provider NEMAPI — API avec contexte géré côté serveur.

NEMAPI reçoit uniquement le message courant et conserve le contexte côté
serveur. Les modèles sont récupérés dynamiquement via ``GET /v1/models``.
"""

import json
import time
from urllib.parse import urlparse
from typing import Dict, Any, List

from providers.base import BaseProvider


class NemapiV3Provider(BaseProvider):
    """Provider NEMAPI (anciennement NEMAPI v3).

    Endpoint principal : POST /v1/chat/completions (stream=false).
    Config : section nemapi {host, port, model} ou provider {host, port, model}.
    
    Modèles disponibles : ceux retournés par ``/v1/models``.
    """

    DEFAULT_HOST = "127.0.0.1"
    DEFAULT_PORT = 8090
    DEFAULT_MODEL = "qwen-chat"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        import requests as req

        self.req = req
        nv3_cfg = config.get("nemapi", config.get("nemapi_v3", {}))
        configured_url = (
            nv3_cfg.get("url")
            or self.provider_config.get("url")
            or ""
        ).strip()
        parsed_url = urlparse(configured_url if "://" in configured_url else f"http://{configured_url}") if configured_url else None
        self.host = (
            nv3_cfg.get("host")
            or self.provider_config.get("host")
            or (parsed_url.hostname if parsed_url else None)
            or self.DEFAULT_HOST
        )
        self.port = int(
            nv3_cfg.get("port")
            or self.provider_config.get("port")
            or (parsed_url.port if parsed_url else None)
            or self.DEFAULT_PORT
        )
        self.base_url = f"http://{self.host}:{self.port}"
        
        # Le modèle configuré sera validé contre /v1/models lorsqu'il est interrogé.
        self.model = (
            nv3_cfg.get("model")
            or self.provider_config.get("model")
            or self.DEFAULT_MODEL
        )
        
        self._models_cache: List[Dict[str, Any]] = []
        
        self.timeout = int(self.provider_config.get("timeout", 180))
        self.max_retries = int(self.provider_config.get("max_retries", 3))
        self.retry_delay = float(self.provider_config.get("retry_delay", 2))
        # Only transient errors are worth retrying; a 4xx is a client/config
        # mistake that will not fix itself by repeating the request.
        self._retryable_status = {429, 500, 502, 503, 504}
        self._conversation: List[Dict[str, str]] = []
        self._session_started = False

    def test_connection(self) -> bool:
        """Teste /status puis fallback sur la racine."""
        try:
            resp = self.req.get(f"{self.base_url}/status", timeout=5)
            if resp.status_code == 200:
                return True
        except Exception:
            pass
        try:
            self.req.head(f"{self.base_url}/", timeout=5)
            return True
        except Exception:
            return False

    def list_models(self) -> List[Dict[str, Any]]:
        """Liste les modèles exposés par le serveur NEMAPI."""
        try:
            resp = self.req.get(f"{self.base_url}/v1/models", timeout=10)
            resp.raise_for_status()
            data = resp.json()
            raw_models = data.get("data", data) if isinstance(data, dict) else data
            models = []
            for item in raw_models or []:
                if isinstance(item, str):
                    models.append({"id": item, "owned_by": "nemapi"})
                elif isinstance(item, dict) and item.get("id"):
                    models.append({
                        "id": str(item["id"]),
                        "owned_by": item.get("owned_by", "nemapi"),
                        "display_name": item.get("display_name", str(item["id"]).replace("-", " ").title()),
                    })
            self._models_cache = models
            # Do NOT silently mutate the active model when listing; that made
            # /models report a different model than the saved config. The
            # caller (models_command) is responsible for choosing one.
            return models
        except Exception:
            # Swallow listing failures but still surface the cached value so
            # /models can present something instead of an empty surprise.
            return list(self._models_cache)

    def send_message(self, message: str, role: str = "user") -> Dict[str, Any]:
        """Envoie uniquement le message courant au format OpenAI.
        Le contexte est géré côté API/Bridge, pas ici.
        
        NEMAPI v3 ne gère pas le contexte incrémental, donc on envoie
        uniquement le message courant sans historique.
        """
        if role == "tool_result":
            send_content = (
                "[TOOL RESULT]\n"
                f"{message}"
            )
            send_role = "user"
        elif role == "system":
            send_content = "[SYSTEM INSTRUCTIONS]\n" + message
            send_role = "system"
        else:
            send_role = role
            send_content = message

        last_error = ""
        for attempt in range(1, self.max_retries + 1):
            try:
                payload = {
                    "model": self.model,
                    "messages": [{"role": send_role, "content": send_content}],
                    "stream": False,
                    # Start Nemesis on a clean provider chat, then keep the
                    # browser-native context for tool-result iterations.
                    "fresh_chat": not self._session_started,
                }
                resp = self.req.post(
                    f"{self.base_url}/v1/chat/completions",
                    json=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=self.timeout,
                )

                if resp.status_code != 200:
                    last_error = f"HTTP {resp.status_code}: {resp.text[:500]}"
                    if resp.status_code in self._retryable_status and attempt < self.max_retries:
                        # Exponential backoff for transient server errors.
                        time.sleep(self.retry_delay * (2 ** (attempt - 1)))
                        continue
                    return {"success": False, "error": last_error}

                text = resp.text
                
                # D'abord, essayer de parser comme JSON normal (non-streaming)
                try:
                    data = json.loads(text)
                    if isinstance(data, dict) and 'choices' in data:
                        reply = self._extract_reply(data)
                        self._session_started = True
                        return {"success": True, "response": reply}
                except json.JSONDecodeError:
                    pass
                
                # Si ce n'est pas du JSON normal, vérifier si c'est du streaming SSE
                if "data: {" in text or "data:{" in text:
                    full_content = ""
                    for line in text.split('\n'):
                        line = line.strip()
                        if line.startswith('data: '):
                            chunk_data = line[6:]
                        elif line.startswith('data:'):
                            chunk_data = line[5:]
                        else:
                            continue
                            
                        if chunk_data == '[DONE]':
                            continue
                        try:
                            chunk = json.loads(chunk_data)
                            if 'choices' in chunk and chunk['choices']:
                                delta = chunk['choices'][0].get('delta', {})
                                content = delta.get('content', '')
                                if content:
                                    full_content += content
                        except json.JSONDecodeError:
                            continue
                    
                    if full_content:
                        self._session_started = True
                        return {"success": True, "response": full_content}
                
                # Si aucun contenu n'a été extrait, retourner une erreur
                return {"success": False, "error": f"Aucun contenu extrait de la réponse: {text[:200]}"}

            except self.req.exceptions.Timeout:
                last_error = f"Timeout après {self.timeout}s"
            except self.req.exceptions.ConnectionError as e:
                last_error = f"Connexion refusée: {e}"
            except Exception as e:
                last_error = str(e)

            if attempt < self.max_retries:
                time.sleep(self.retry_delay)

        return {
            "success": False,
            "error": f"Échec après {self.max_retries} tentatives: {last_error}",
        }

    def _extract_reply(self, data: Any) -> str:
        """Extrait le texte assistant, y compris tool_calls sérialisés."""
        if not isinstance(data, dict):
            # Si data n'est pas un dict, c'est probablement une réponse non-structurée
            return str(data)
        
        choices = data.get("choices") or []
        if not choices:
            if isinstance(data.get("response"), str):
                return data["response"]
            if isinstance(data.get("content"), str):
                return data["content"]
            return json.dumps(data, ensure_ascii=False)

        choice = choices[0]
        message = choice.get("message") or {}
        content = message.get("content") or ""
        tool_calls = message.get("tool_calls")

        # Si NEMAPI a déjà parsé des tool_calls OpenAI, on les reformate
        # en bloc ```json pour le parseur de l'agent.
        if tool_calls:
            parts = []
            if content and str(content).strip():
                parts.append(str(content).strip())
            for tc in tool_calls:
                fn = tc.get("function") or {}
                name = fn.get("name", "")
                raw_args = fn.get("arguments", "{}")
                if isinstance(raw_args, str):
                    try:
                        args = json.loads(raw_args)
                    except json.JSONDecodeError:
                        args = {"_raw": raw_args}
                elif isinstance(raw_args, dict):
                    args = raw_args
                else:
                    args = {}
                if name:
                    tool_obj = {"tool": name, "parameters": args}
                    parts.append(
                        "```json\n"
                        + json.dumps(tool_obj, ensure_ascii=False, indent=2)
                        + "\n```"
                    )
            return "\n\n".join(parts) if parts else content

        if content:
            return str(content)

        # Fallback delta / text
        for key in ("text", "response", "result"):
            if key in choice and choice[key]:
                return str(choice[key])
        return ""

    def set_model(self, model: str) -> bool:
        """Change le modèle utilisé."""
        available = {item["id"] for item in self.list_models()}
        if model in available:
            self.model = model
            return True
        return False

    def get_available_models(self) -> List[str]:
        """Retourne la liste des modèles disponibles."""
        return [item["id"] for item in self.list_models()]


NemapiProvider = NemapiV3Provider
