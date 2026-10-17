#!/usr/bin/env python3
"""Provider NEMAPI — API avec contexte géré côté serveur.

NEMAPI reçoit uniquement le message courant et conserve le contexte côté
serveur. Les modèles sont récupérés dynamiquement via ``GET /v1/models``.
"""

import json
import time
from urllib.parse import urlparse
from typing import Dict, Any, List, Optional

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
        # Persistent HTTP session enables keep-alive / connection pooling,
        # removing a TCP+TLS handshake on every turn.
        try:
            self._session = req.Session()
        except Exception:
            self._session = None
        self._conn_cached: Optional[bool] = None
        self._conn_checked_at = 0.0
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
        # A public URL (e.g. https://xxx.trycloudflare.com/v1, or with a custom
        # prefix such as https://host/nemapi/v1) may carry a scheme, host, port
        # and a path prefix. We keep the prefix but drop a trailing /v1 since
        # every endpoint below already appends /v1/... explicitly.
        if parsed_url and parsed_url.netloc:
            prefix = (parsed_url.path or "").rstrip("/")
            if prefix.endswith("/v1"):
                prefix = prefix[: -len("/v1")]
            self.base_url = f"{parsed_url.scheme}://{parsed_url.netloc}{prefix}"
        else:
            self.base_url = f"http://{self.host}:{self.port}"
        self.url = self.base_url
        
        # Le modèle configuré sera validé contre /v1/models lorsqu'il est interrogé.
        self.model = (
            nv3_cfg.get("model")
            or self.provider_config.get("model")
            or self.DEFAULT_MODEL
        )
        self.api_token = str(nv3_cfg.get("token") or "").strip()
        
        self._models_cache: List[Dict[str, Any]] = []
        
        configurable_timeout = (
            nv3_cfg.get("timeout")
            or self.provider_config.get("timeout")
            or 180
        )
        self.timeout = int(configurable_timeout)
        self.max_retries = 3
        self.retry_delay = 1
        self._conversation: List[Dict[str, str]] = []
        # NemApi keeps the conversation in the browser tab. ``fresh_chat`` is
        # ALWAYS sent as False (see ``send_message``): NEMESIS never asks the
        # extension to reload the provider page. ``_prompt_sent`` only tracks
        # whether the system prompt / tool catalogue was already delivered for
        # this logical session, so a resumed session does not resend it.
        self.session_id: Optional[str] = None
        self._prompt_sent = False

    def configure_session(self, session_id: Optional[str], initialized: bool = False) -> None:
        """Bind this provider instance to a persisted logical session.

        ``initialized`` reflects whether the system prompt was already sent for
        this session. When True the prompt is suppressed on resume. ``fresh_chat``
        is never affected: it is always False regardless of this call.
        """
        self.session_id = session_id or None
        self._prompt_sent = bool(initialized)

    def test_connection(self, max_age: float = 20.0) -> bool:
        """Teste /status puis /v1/models puis la racine.

        Le resultat est mis en cache ``max_age`` secondes pour eviter un
        aller-retour reseau a chaque message. Le premier probe repond souvent
        instantanement ; on tente les suivants seulement en cas d'echec.
        """
        now = time.time()
        if self._conn_cached is not None and (now - self._conn_checked_at) < max_age:
            return self._conn_cached
        getter = self._session.get if self._session is not None else self.req.get
        ok = False
        for probe in (
            f"{self.base_url}/status",
            f"{self.base_url}/v1/models",
            self.base_url + "/",
        ):
            try:
                resp = getter(probe, timeout=3, headers=self._auth_headers())
                if resp.status_code < 500:
                    ok = True
                    break
            except Exception:
                continue
        self._conn_cached = ok
        self._conn_checked_at = now
        return ok

    def list_models(self) -> List[Dict[str, Any]]:
        """Liste les modèles exposés par le serveur NEMAPI.

        Tolère plusieurs formes de réponse : ``{"data": [...]}``, une liste
        brute, une liste de chaînes, ou un dict ``{id: {...}}``. Erreurs réseau
        remontées explicitement pour que l'UI puisse distinguer « hors ligne »
        d'une simple réponse vide.
        """
        getter = self._session.get if self._session is not None else self.req.get
        resp = getter(
            f"{self.base_url}/v1/models",
            timeout=10,
            headers=self._auth_headers(),
        )
        resp.raise_for_status()
        data = resp.json()
        raw_models = data.get("data", data) if isinstance(data, dict) else data

        items: List[Any]
        if isinstance(raw_models, dict):
            # {id: {...}} or {models: [...]} shapes
            if isinstance(raw_models.get("models"), list):
                items = raw_models["models"]
            else:
                items = []
                for key, value in raw_models.items():
                    if isinstance(value, dict):
                        value = {"id": key, **value}
                    else:
                        value = {"id": key}
                    items.append(value)
        elif isinstance(raw_models, list):
            items = raw_models
        else:
            items = []

        models: List[Dict[str, Any]] = []
        for item in items:
            if isinstance(item, str):
                models.append({"id": item, "owned_by": "nemapi"})
            elif isinstance(item, dict) and item.get("id"):
                models.append({
                    "id": str(item["id"]),
                    "owned_by": item.get("owned_by", "nemapi"),
                    "display_name": item.get("display_name")
                    or str(item["id"]).replace("-", " ").replace("_", " ").title(),
                })

        self._models_cache = models
        if models and self.model not in {m["id"] for m in models}:
            self.model = models[0]["id"]
        return models

    def send_message(
        self,
        message: str,
        role: str = "user",
        cancel_event=None,
        system_prefix: str = "",
    ) -> Dict[str, Any]:
        """Envoie uniquement le message courant au format OpenAI.
        Le contexte est géré côté API/Bridge, pas ici.

        NEMAPI v3 ne gère pas le contexte incrémental, donc on envoie
        uniquement le message courant sans historique.

        ``system_prefix`` permet de fusionner les instructions système et le
        premier message utilisateur en une SEULE requête HTTP (gain de latence
        au démarrage d'une session). Il n'est appliqué qu'une seule fois par
        conversation logique : le prompt système et le catalogue d'outils ne
        sont JAMAIS renvoyés sur une reprise de session.

        ``cancel_event`` (optionnel) permet d'interrompre une requête bloquante
        depuis un autre thread (bouton « Interrompre » de l'UI web).
        """
        prefix = "" if self._prompt_sent else (system_prefix or "").strip()
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
            if prefix:
                # Merge system instructions + first user message into one request.
                send_content = (
                    "[SYSTEM INSTRUCTIONS]\n" + prefix +
                    "\n\n[USER MESSAGE]\n" + message
                )

        last_error = ""
        for attempt in range(1, self.max_retries + 1):
            if cancel_event is not None and cancel_event.is_set():
                return {"success": False, "error": "Requête interrompue par l'utilisateur."}
            try:
                payload = {
                    "model": self.model,
                    "messages": [{"role": send_role, "content": send_content}],
                    "stream": False,
                    # ALWAYS false: NemApi's ``fresh_chat`` makes the browser
                    # extension reload the provider page (a brand-new chat URL).
                    # NEMESIS must never trigger that reload — neither for a new
                    # conversation nor when resuming one. Conversation lifecycle
                    # is handled by the user on the NemApi side.
                    "fresh_chat": False,
                }
                resp = self._post_cancellable(payload, cancel_event)
                if resp is None:
                    return {"success": False, "error": "Requête interrompue par l'utilisateur."}

                if resp.status_code != 200:
                    last_error = f"HTTP {resp.status_code}: {resp.text[:500]}"
                    if attempt < self.max_retries:
                        time.sleep(self.retry_delay)
                        continue
                    return {"success": False, "error": last_error}

                text = resp.text
                
                # D'abord, essayer de parser comme JSON normal (non-streaming)
                try:
                    data = json.loads(text)
                    if isinstance(data, dict) and 'choices' in data:
                        reply = self._extract_reply(data)
                        self._prompt_sent = True
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
                        self._prompt_sent = True
                        return {"success": True, "response": full_content}
                
                # Si aucun contenu n'a été extrait, retourner une erreur
                return {"success": False, "error": f"Aucun contenu extrait de la réponse: {text[:200]}"}

                # Pas d'historique local inutile — on se comporte comme un bridge stateless
                return {"success": True, "response": reply}

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

    def _post_cancellable(self, payload: Dict[str, Any], cancel_event):
        """POST bloquant, interruptible via ``cancel_event``.

        La requête s'exécute dans un thread dédié ; on attend son résultat par
        petits pas afin de pouvoir abandonner sans tuer le thread sous-jacent
        (limitation de ``requests``/urllib3).
        """
        import threading

        result: Dict[str, Any] = {}

        poster = self._session.post if self._session is not None else self.req.post

        def worker():
            try:
                result["resp"] = poster(
                    f"{self.base_url}/v1/chat/completions",
                    json=payload,
                    headers={"Content-Type": "application/json", **self._auth_headers()},
                    timeout=(10, self.timeout),
                )
            except Exception as exc:  # noqa: BLE001
                result["exc"] = exc

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        while thread.is_alive():
            # Small poll interval keeps interrupt latency low without adding
            # measurable delay to fast responses.
            thread.join(timeout=0.02)
            if cancel_event is not None and cancel_event.is_set():
                return None
        if "exc" in result:
            raise result["exc"]
        return result.get("resp")

    def _auth_headers(self) -> Dict[str, str]:
        if not self.api_token:
            return {}
        return {"Authorization": f"Bearer {self.api_token}"}

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
