#!/usr/bin/env python3
"""Bot Telegram NEMESIS-CLI — interface distante de l'agent autonome.

Structure de configuration (identique au CLI NEMESIS) :
- provider.type: toujours ``nemapi``
- nemapi: {host, port, model}
- security.workspace: repertoire de travail

Les configurations du bot sont stockees dans telegram_bot_configs/{chat_id}.yaml
Separation totale des configurations CLI et bot.
"""

import os
import sys
import json
import logging
import asyncio
import re
import tempfile
import time
import uuid
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from types import SimpleNamespace

if getattr(sys, "frozen", False):
    script_dir = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
else:
    script_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(script_dir))

from telegram import Update, Message, Bot, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    CallbackContext,
    CallbackQueryHandler,
)

# Import des modules NEMESIS
from src.core.agent_tools import create_registry
from providers import create_provider, list_providers
from tools import ActionExecutor
from src.core.tool_bridge import ToolBridge
from src.core.telegram_format import markdown_to_plain_text
from tools_schema import get_tool_handler_method, validate_tool_call
from action_parser import ActionParser

# Configuration du logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# =============================================================================
# CONSTANTES
# =============================================================================

PROVIDER_LABELS = {
    "nemapi": "NEMAPI",
}

# Providers qui gerent leur contexte cote serveur
SERVER_MANAGED_PROVIDERS = ["nemapi"]
MAX_TELEGRAM_DOWNLOAD_BYTES = 20 * 1024 * 1024
MAX_TELEGRAM_SEND_BYTES = 50 * 1024 * 1024
TELEGRAM_SYSTEM_PROMPT_EXTENSION = """

## Telegram-specific attachments and file delivery
- User attachments arrive in the Telegram message with their local file path stated at the top of the message. Read attached non-image files with `read_file` (or a suitable tool for the format) before answering. Images are also passed as multimodal data URLs when supported: inspect them visually and never dump/read their binary bytes.
- When the user asks you to create a document or other file and receive it in this chat, create and verify it, then call `send_file_to_user` with its path (and an optional short caption). Do not merely mention a path; the file must be sent through the Telegram chat. Never claim delivery unless the tool confirms success.
"""

# Etats de conversation
AWAITING_PROVIDER_CONFIG = 1
AWAITING_MODEL_SELECTION = 2
AWAITING_BRIDGE_HOST = 4
AWAITING_NEMAPI_HOST = 5

# Repertoire de stockage des configurations du bot
def _resolve_bot_config_dir() -> Path:
    try:
        from src.core.paths import USER_CONFIG_DIR
        return Path(USER_CONFIG_DIR) / "telegram_bot_configs"
    except Exception:
        return Path.home() / ".config" / "nemesis-cli" / "telegram_bot_configs"


BOT_CONFIG_DIR = _resolve_bot_config_dir()


# =============================================================================
# GESTION DE LA CONFIGURATION PERSISTANTE
# =============================================================================

def ensure_config_dir():
    """Assure que le repertoire de configuration existe."""
    BOT_CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def get_chat_config_path(chat_id: int) -> Path:
    """Retourne le chemin du fichier de configuration pour un chat."""
    return BOT_CONFIG_DIR / f"{chat_id}.yaml"


def load_chat_config(chat_id: int) -> Dict[str, Any]:
    """Charge la configuration pour un chat depuis le disque."""
    ensure_config_dir()
    config_path = get_chat_config_path(chat_id)
    
    # Configuration par defaut (identique au CLI)
    default_config = {
        "provider": {"type": "nemapi"},
        "nemapi": {"host": "127.0.0.1", "port": 8080, "model": ""},
        "security": {"workspace": str(Path.cwd())},
    }
    
    if not config_path.exists():
        return default_config
    
    try:
        import yaml
        with open(config_path, 'r') as f:
            loaded = yaml.safe_load(f)
            if not loaded:
                return default_config
            if not isinstance(loaded, dict):
                loaded = {}
            # Migrate old per-chat configurations to the single public provider.
            legacy = loaded.get("nemapi_v3") or loaded.get("nemapi_bridge") or loaded.get("bridge") or {}
            provider = loaded.get("provider") if isinstance(loaded.get("provider"), dict) else {}
            nemapi = loaded.get("nemapi") if isinstance(loaded.get("nemapi"), dict) else {}
            if legacy and not nemapi:
                nemapi = dict(legacy)
            for key in ("host", "port", "model"):
                if provider.get(key) is not None and nemapi.get(key) in (None, ""):
                    nemapi[key] = provider[key]
            security = loaded.get("security") if isinstance(loaded.get("security"), dict) else {}
            normalized = {
                "provider": {"type": "nemapi"},
                "nemapi": {
                    "host": str(nemapi.get("host") or "127.0.0.1"),
                    "port": int(nemapi.get("port") or 8080),
                    "model": str(nemapi.get("model") or ""),
                },
                "security": {
                    "workspace": str(
                        security.get("workspace") or default_config["security"]["workspace"]
                    ),
                },
            }
            if loaded.get("session_id"):
                normalized["session_id"] = str(loaded["session_id"])
            return normalized
    except (OSError, ValueError, TypeError, yaml.YAMLError) as e:
        logger.error(f"Erreur chargement config pour chat {chat_id}: {e}")
        return default_config


def save_chat_config(chat_id: int, config: Dict[str, Any]) -> bool:
    """Sauvegarde la configuration pour un chat sur le disque."""
    ensure_config_dir()
    config_path = get_chat_config_path(chat_id)
    
    try:
        import yaml
        with open(config_path, 'w') as f:
            yaml.dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        return True
    except Exception as e:
        logger.error(f"Erreur sauvegarde config pour chat {chat_id}: {e}")
        return False


# =============================================================================
# CLASSE PRINCIPALE DU BOT
# =============================================================================

class NemesisTelegramBot:
    """Bot Telegram qui integre l'agent NEMESIS-CLI."""

    def __init__(self, token: str, workspace: str | None = None,
                 default_config: Optional[Dict[str, Any]] = None):
        """Initialise le bot Telegram NEMESIS."""
        self.token = token
        self.workspace = workspace or str(Path.cwd())
        defaults = default_config or {}
        default_nemapi = defaults.get("nemapi", {}) if isinstance(defaults, dict) else {}
        self.default_nemapi = {
            "host": str(default_nemapi.get("host") or "127.0.0.1"),
            "port": int(default_nemapi.get("port") or 8080),
            "model": str(default_nemapi.get("model") or ""),
        }
        self.bot: Optional[Bot] = None
        self.app: Optional[Application] = None
        self.registry = None
        self.executor = ToolBridge(workspace=self.workspace, scheduler_owner_id="telegram")
        self.parser = ActionParser(extra_valid_tools={"send_file_to_user"}, strict=True)
        self._event_loop = None
        from src.core.scheduler import get_scheduler

        get_scheduler().register_handler("telegram:*", self._run_scheduled_task)
        self.providers: Dict[int, Any] = {}
        self._feedback_sequences: Dict[int, int] = {}
        self._chat_message_counts: Dict[int, int] = {}
        self._started_at = time.time()
        
        # Etats de conversation par chat
        self.chat_states: Dict[int, int] = {}
        
        # Donnees temporaires
        
        # Suivi du prompt système envoyé par chat
        self.prompt_system_sent: Dict[int, bool] = {}
        
        # Initialiser NEMESIS
        self._init_nemesis()
        
        # S'assurer que le repertoire de config existe
        ensure_config_dir()

    def _init_nemesis(self):
        """Initialise NEMESIS et ajoute l'outil exclusif de livraison Telegram."""
        self.registry = self.executor.registry
        if "send_file_to_user" not in self.registry._tools:
            from src.core.tool_kind import ToolKind

            self.registry.register(
                name="send_file_to_user",
                kind=ToolKind.OTHER,
                handler=self._prepare_telegram_file,
                description=(
                    "Send a verified local file to the user in this Telegram chat. "
                    "Use after creating or locating an artifact that the user asked to receive. "
                    "This tool exists only in Telegram; it uploads the file as a Telegram document."
                ),
                params_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Path to the file to deliver."},
                        "caption": {"type": "string", "description": "Optional short message shown with the file."},
                    },
                    "required": ["path"],
                },
                risk="high",
            )
        logger.info("NEMESIS agent initialized with %d tools", len(self.registry._tools))

    def _prepare_telegram_file(self, path: str, caption: str = "") -> Dict[str, Any]:
        """Validate a local artifact before the Telegram API uploads it."""
        if not isinstance(path, str) or not path.strip():
            return {"success": False, "error": "Un chemin de fichier est requis."}
        try:
            file_path = Path(path).expanduser().resolve(strict=True)
            if not file_path.is_file():
                return {"success": False, "error": "Le chemin indiqué n'est pas un fichier."}
            size = file_path.stat().st_size
            if size > MAX_TELEGRAM_SEND_BYTES:
                return {
                    "success": False,
                    "error": f"Le fichier dépasse la limite Telegram de {MAX_TELEGRAM_SEND_BYTES // (1024 * 1024)} Mio.",
                }
            return {
                "success": True,
                "stdout": f"Fichier prêt à être envoyé : {file_path.name} ({size} octets).",
                "telegram_file_path": str(file_path),
                "telegram_file_name": file_path.name[:255],
                "telegram_caption": str(caption or "")[:1024],
                "size": size,
            }
        except (OSError, RuntimeError, ValueError) as exc:
            return {"success": False, "error": f"Fichier inaccessible : {exc}"}

    def _get_chat_config(self, chat_id: int) -> Dict[str, Any]:
        """Charge la configuration pour un chat."""
        config = load_chat_config(chat_id)
        path = get_chat_config_path(chat_id)
        if not path.exists():
            config["nemapi"].update(self.default_nemapi)
            config["security"]["workspace"] = self.workspace
            save_chat_config(chat_id, config)
        return config

    def _save_chat_config(self, chat_id: int, config: Dict[str, Any]) -> bool:
        """Sauvegarde la configuration pour un chat."""
        return save_chat_config(chat_id, config)

    def _get_provider(self, chat_id: int) -> Any:
        """Cree et retourne le provider pour un chat."""
        if chat_id in self.providers:
            return self.providers[chat_id]
        config = self._get_chat_config(chat_id)
        try:
            provider = create_provider(config)
            session_id = config.get("session_id")
            if session_id and hasattr(provider, "configure_session"):
                try:
                    from web.backend.sessions import is_initialized

                    initialized = is_initialized(session_id)
                except Exception:
                    initialized = False
                provider.configure_session(session_id, initialized=initialized)
                self.prompt_system_sent[chat_id] = initialized
            self.providers[chat_id] = provider
            return provider
        except Exception as e:
            logger.error(f"Erreur creation provider pour chat {chat_id}: {e}")
            return None

    def _needs_provider_config(self, chat_id: int) -> bool:
        """Verifie si le provider a besoin d'etre configure."""
        config = self._get_chat_config(chat_id)
        return config.get("provider", {}).get("type") != "nemapi"

    def _get_default_model_for_provider(self, provider_type: str) -> str:
        """Retourne le modele par defaut pour un provider donne."""
        return "qwen-chat" if provider_type == "nemapi" else ""
    
    def _needs_model_selection(self, chat_id: int) -> bool:
        """Verifie si le modele doit etre selectionne."""
        config = self._get_chat_config(chat_id)
        return not config.get("nemapi", {}).get("model")


    # =============================================================================
    # COMMANDES SLASH
    # =============================================================================

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Gère /start et présente la liste de commandes synchronisée au CLI."""
        chat_id = update.effective_chat.id
        user = update.effective_user
        name = getattr(user, "first_name", None) or ""
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"Bonjour {name} ! NEMESIS est prêt. Configurez NEMAPI avec /provider, puis le modèle avec /model. Tous les outils, y compris les opérations sensibles, sont exécutés sans confirmation.",
        )
        await self.help_command(update, context)

    def _cli_command_catalog(self):
        """Return the registered CLI commands in Telegram-safe slash form."""
        from src.core import default_commands  # noqa: F401 - populates the shared registry
        from src.core.commands import registry

        specs = []
        seen = set()
        for command in registry.list_commands():
            name = command.name.replace(".", "_")
            if not name.isascii() or not name.replace("_", "").isalnum() or len(name) > 32:
                continue
            if name in seen:
                continue
            seen.add(name)
            specs.append((name, command.name, command.description or command.name))
        return specs

    def _telegram_command_specs(self):
        specs = [
            ("start", "start", "Démarrer NEMESIS"),
            ("provider", "provider", "Configurer le serveur NEMAPI"),
            ("new", "new", "Créer une nouvelle conversation"),
        ]
        known = {name for name, _, _ in specs}
        for name, original, description in self._cli_command_catalog():
            if name not in known:
                specs.append((name, original, description[:256]))
                known.add(name)
        return specs

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Liste les commandes de la CLI partagées avec l'interface Telegram."""
        chat_id = update.effective_chat.id
        lines = ["Commandes NEMESIS — Telegram et CLI", ""]
        for telegram_name, original, description in self._telegram_command_specs():
            suffix = f" (commande CLI /{original})" if telegram_name != original else ""
            lines.append(f"/{telegram_name} — {description}{suffix}")
        lines.extend([
            "",
            "Commandes propres au bot : /provider, /new, /start.",
            "Les outils sont exécutés directement, sans demande de confirmation.",
        ])
        await self._send_long_message(context, chat_id, "\n".join(lines))

    async def cli_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Adaptateur Telegram des commandes du registre CLI."""
        chat_id = update.effective_chat.id
        raw = (getattr(update.effective_message, "text", "") or "").split(maxsplit=1)[0]
        command_name = raw.lstrip("/").split("@", 1)[0].lower()
        command_name = {"nemesis_md": "nemesis.md"}.get(command_name, command_name)
        args = list(getattr(context, "args", []) or [])
        config = self._get_chat_config(chat_id)

        if command_name in {"models", "model"}:
            await self.model_command(update, context)
            return
        if command_name == "help":
            await self.help_command(update, context)
            return
        if command_name == "about":
            await self.about_command(update, context)
            return
        if command_name == "tools":
            await self.tools_command(update, context)
            return
        if command_name == "config":
            await self.config_command(update, context)
            return
        if command_name == "clear":
            await self.clear_command(update, context)
            return
        if command_name == "history":
            await self.history_command(update, context)
            return
        if command_name in {"exit", "quit"}:
            await context.bot.send_message(
                chat_id=chat_id,
                text="Le bot reste actif pour les autres conversations. Utilisez /new pour terminer cette conversation.",
            )
            return
        if command_name == "continue":
            self.prompt_system_sent[chat_id] = True
            await context.bot.send_message(chat_id=chat_id, text="La prochaine requête poursuivra le contexte courant, sans renvoyer le prompt système.")
            return
        if command_name == "status":
            provider = self._get_provider(chat_id)
            security = config.get("security", {})
            lines = [
                "État de la session Telegram",
                f"Connexion : {'provider créé' if provider else 'provider indisponible (non testé)'}",
                "Provider : NEMAPI",
                f"Modèle : {config.get('nemapi', {}).get('model') or 'non sélectionné'}",
                f"Endpoint : {config.get('nemapi', {}).get('host', '127.0.0.1')}:{config.get('nemapi', {}).get('port', 8080)}",
                f"Workspace : {security.get('workspace', self.workspace)}",
                "Autorisations : désactivées (exécution directe de tous les outils)",
            ]
            await self._send_long_message(context, chat_id, "\n".join(lines))
            return
        if command_name == "stats":
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"Statistiques Telegram\nMessages reçus : {self._chat_message_counts.get(chat_id, 0)}",
            )
            return
        if command_name == "approval":
            await context.bot.send_message(
                chat_id=chat_id,
                text="Les confirmations d'outils sont désactivées : tous les outils s'exécutent directement.",
            )
            return
        if command_name in {"sessions", "session"}:
            from web.backend import sessions as session_store

            operation = args[0].lower() if args else "list"
            if operation == "new":
                title = " ".join(args[1:]).strip() or "Conversation Telegram"
                model = config.get("nemapi", {}).get("model", "")
                session = session_store.create_session(title=title, model=model)
                session["telegram_chat_id"] = str(chat_id)
                session_store.save_session(session)
                config["session_id"] = session["id"]
                self._save_chat_config(chat_id, config)
                provider = self._get_provider(chat_id)
                if provider and hasattr(provider, "configure_session"):
                    provider.configure_session(session["id"], initialized=False)
                self.prompt_system_sent[chat_id] = False
                await context.bot.send_message(chat_id=chat_id, text=f"Nouvelle session créée : {session['id']} — {title}")
                return
            if operation == "open" and len(args) > 1:
                session = session_store.get_session(args[1])
                if not session or session.get("telegram_chat_id") != str(chat_id):
                    await context.bot.send_message(chat_id=chat_id, text="Session introuvable.")
                    return
                config["session_id"] = session["id"]
                self._save_chat_config(chat_id, config)
                provider = self._get_provider(chat_id)
                initialized = session_store.is_initialized(session["id"])
                if provider and hasattr(provider, "configure_session"):
                    provider.configure_session(session["id"], initialized=initialized)
                self.prompt_system_sent[chat_id] = initialized
                await context.bot.send_message(chat_id=chat_id, text=f"Session ouverte : {session['id']} — {session.get('title', '')}")
                return
            if operation == "delete" and len(args) > 1:
                session = session_store.get_session(args[1])
                deleted = bool(
                    session
                    and session.get("telegram_chat_id") == str(chat_id)
                    and session_store.delete_session(args[1])
                )
                if config.get("session_id") == args[1] and deleted:
                    config.pop("session_id", None)
                    self._save_chat_config(chat_id, config)
                    self.prompt_system_sent[chat_id] = False
                await context.bot.send_message(chat_id=chat_id, text="Session archivée." if deleted else "Session introuvable.")
                return
            if operation != "list":
                await context.bot.send_message(chat_id=chat_id, text="Usage : /sessions [list|new [titre]|open <id>|delete <id>]")
                return
            sessions = [
                item for item in session_store.list_sessions()
                if (session_store.get_session(item["id"]) or {}).get("telegram_chat_id") == str(chat_id)
            ]
            if not sessions:
                await context.bot.send_message(chat_id=chat_id, text="Aucune session Telegram enregistrée. Utilisez /sessions new.")
                return
            lines = ["Vos sessions Telegram :"]
            lines.extend(f"{item['id']} — {item['title']} ({item['message_count']} messages)" for item in sessions[:30])
            await self._send_long_message(context, chat_id, "\n".join(lines))
            return
        if command_name == "memory":
            from web.backend import settings as memory_store

            operation = args[0].lower() if args else "list"
            if operation == "list":
                state = memory_store.memory_state()
                lines = [f"Mémoire partagée : {'activée' if state['enabled'] else 'désactivée'}"]
                lines.extend(f"{block.get('id')} — {block.get('title')}: {block.get('content')}" for block in state.get("blocks", []))
                await self._send_long_message(context, chat_id, "\n".join(lines))
            elif operation == "add":
                payload = " ".join(args[1:])
                title, separator, content = payload.partition("|")
                if not separator or not title.strip() or not content.strip():
                    await context.bot.send_message(chat_id=chat_id, text="Usage Telegram : /memory add <titre> | <contenu>")
                else:
                    result = memory_store.memory_upsert(title=title.strip(), content=content.strip())
                    await context.bot.send_message(chat_id=chat_id, text=result.get("message") or ("Bloc enregistré." if result.get("ok") else "Échec."))
            elif operation == "delete" and len(args) > 1:
                result = memory_store.memory_delete(args[1])
                await context.bot.send_message(chat_id=chat_id, text="Bloc supprimé." if result.get("ok") else result.get("message", "Échec."))
            elif operation in {"on", "off"}:
                result = memory_store.memory_set_enabled(operation == "on")
                await context.bot.send_message(chat_id=chat_id, text="Préférence mémoire mise à jour." if result.get("ok") else result.get("message", "Échec."))
            elif operation == "clear" and len(args) > 1 and args[1].lower() == "confirm":
                result = memory_store.memory_clear()
                await context.bot.send_message(chat_id=chat_id, text="Mémoire effacée." if result.get("ok") else result.get("message", "Échec."))
            else:
                await context.bot.send_message(chat_id=chat_id, text="Usage : /memory [list|add <titre> | <contenu>|delete <id>|on|off|clear confirm]")
            return
        if command_name == "todo":
            try:
                from src.core.tools.todo import _load

                todo = _load(config.get("security", {}).get("workspace", self.workspace))
                await self._send_long_message(context, chat_id, json.dumps(todo, ensure_ascii=False, indent=2) if todo else "Aucune tâche dans le plan.")
            except (OSError, TypeError, ValueError) as exc:
                await context.bot.send_message(chat_id=chat_id, text=f"Impossible de charger le plan : {exc}")
            return
        if command_name == "files":
            from pathlib import Path
            from src.core.paths import resolve_path

            raw_path = " ".join(args).strip() or "."
            try:
                target = Path(resolve_path(raw_path))
                if not target.is_dir():
                    await context.bot.send_message(chat_id=chat_id, text=f"Dossier introuvable : {target}")
                    return
                entries = sorted(target.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))[:100]
                listing = [f"{target}/"] + [f"{'[dir]' if item.is_dir() else '[file]'} {item.name}" for item in entries]
                await self._send_long_message(context, chat_id, "\n".join(listing))
            except (OSError, ValueError) as exc:
                await context.bot.send_message(chat_id=chat_id, text=f"Impossible de lire ce dossier : {exc}")
            return
        if command_name == "show":
            await context.bot.send_message(chat_id=chat_id, text="Les sorties des outils sont affichées avec la réponse de l'agent; aucune sortie CLI cachée n'est disponible dans Telegram.")
            return
        if command_name in {"integrations", "mcp", "skills", "subagents", "agents", "agent_status", "a2a", "delegate"}:
            await self._run_auxiliary_cli_command(command_name, args, chat_id, context, config)
            return
        if command_name == "nemesis.md":
            await self._process_ai_request(
                update, context,
                "Génère un fichier NEMESIS.md à la racine du projet pour documenter les objectifs, la structure, les technologies et l'avancement. Utilise les outils disponibles.",
                chat_id,
            )
            return
        if command_name in {"clear", "history", "about", "tools", "config"}:
            return

        await context.bot.send_message(
            chat_id=chat_id,
            text=f"/{command_name} est connue du CLI mais n'a pas d'équivalent interactif dans Telegram. Utilisez le CLI pour cette opération.",
        )

    async def _run_auxiliary_cli_command(self, command_name, args, chat_id, context, config):
        """Adapt read-only and delegation commands whose CLI UI is terminal-bound."""
        if command_name == "agents":
            await context.bot.send_message(chat_id=chat_id, text="Les sous-agents sont créés à la demande. Utilisez /delegate <instruction>, puis /agent_status [id].")
            return
        if command_name in {"agent_status", "a2a"}:
            fleet = getattr(self.executor, "subagent_fleet", None)
            if fleet is None:
                await context.bot.send_message(chat_id=chat_id, text="Flotte de sous-agents indisponible.")
                return
            task_id = args[0] if args else ""
            await self._send_long_message(context, chat_id, json.dumps(fleet.status(task_id), ensure_ascii=False, indent=2))
            return
        if command_name == "delegate":
            instruction = " ".join(args).strip()
            fleet = getattr(self.executor, "subagent_fleet", None)
            if not instruction or fleet is None:
                await context.bot.send_message(chat_id=chat_id, text="Usage : /delegate <instruction>. Flotte de sous-agents indisponible si aucun provider de délégation n'est configuré.")
                return
            result = fleet.delegate([{"instruction": instruction}])
            await context.bot.send_message(chat_id=chat_id, text=f"Tâche lancée : {result['tasks'][0]['task_id']}" if result.get("success") else result.get("error", "Échec de la délégation."))
            return
        if command_name == "subagents":
            fleet = getattr(self.executor, "subagent_fleet", None)
            if fleet is None:
                await context.bot.send_message(chat_id=chat_id, text="Flotte de sous-agents indisponible.")
            else:
                await context.bot.send_message(chat_id=chat_id, text=json.dumps(fleet.availability(), ensure_ascii=False, indent=2))
            return
        if command_name == "integrations":
            service = args[0].lower() if args else ""
            try:
                from web.backend import github_settings, telegram_settings
                if service == "github":
                    info = github_settings.public_github_config()
                    text = f"GitHub : {'connecté' if info.get('connected') else 'déconnecté'} — {info.get('name') or '—'} <{info.get('email') or '—'}>"
                elif service == "telegram":
                    info = telegram_settings.public_telegram_config()
                    text = f"Telegram : {'configuré' if info.get('token_set') else 'non configuré'} — {info.get('bot_name') or '—'}"
                else:
                    text = "Usage : /integrations github|telegram"
                await context.bot.send_message(chat_id=chat_id, text=text + "\nLes secrets ne sont jamais affichés ni modifiables depuis le chat.")
            except Exception as exc:
                await context.bot.send_message(chat_id=chat_id, text=f"Impossible de lire les intégrations : {exc}")
            return
        if command_name == "mcp":
            from src.core.default_commands import mcp_mgr

            servers = mcp_mgr.list_servers()
            if not servers:
                text = "Aucun serveur MCP configuré."
            else:
                text = "Serveurs MCP :\n" + "\n".join(f"• {name} — {server.get('description') or server.get('command', '')}" for name, server in servers.items())
            await self._send_long_message(context, chat_id, text)
            return
        if command_name == "skills":
            from src.core.default_commands import skill_mgr
            installed = skill_mgr.list_installed()
            text = "Skills installés :\n" + ("\n".join(f"• {item.get('name')} — {item.get('description', '')}" for item in installed) if installed else "aucun")
            await self._send_long_message(context, chat_id, text)
            return

    async def provider_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Gere la commande /provider."""
        chat_id = update.effective_chat.id
        
        config = self._get_chat_config(chat_id)
        current = config.get("nemapi", {})
        keyboard = [[InlineKeyboardButton("Configurer NEMAPI", callback_data="provider_nemapi")],
                    [InlineKeyboardButton("Annuler", callback_data="cancel")]]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"*[CONFIG] Provider unique : NEMAPI*\n\n"
                 f"Endpoint actuel : {current.get('host', '127.0.0.1')}:{current.get('port', 8080)}\n"
                 "Choisissez Configurer pour modifier l'endpoint.",
            reply_markup=reply_markup,
        )
        
        self.chat_states[chat_id] = AWAITING_PROVIDER_CONFIG

    async def model_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Gere la commande /model."""
        chat_id = update.effective_chat.id
        
        config = self._get_chat_config(chat_id)
        provider_type = "nemapi"
        
        # Vérifier le modele actuel
        current_model = config.get("nemapi", {}).get("model", "")
        
        provider = self._get_provider(chat_id)
        if not provider:
            await context.bot.send_message(
                chat_id=chat_id,
                text="[ERROR] Erreur: Impossible de creer le provider. Verifiez votre configuration.",
            )
            return
        
        try:
            models = provider.list_models()
        except Exception as e:
            logger.error(f"Erreur list_models pour {provider_type}: {e}")
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"[ERROR] Impossible de lister les modeles. Verifiez votre clé API avec /config\n\n"
                     f"Erreur: {str(e)[:200]}",
            )
            return
        
        # Filtrer les doublons
        seen_models = set()
        unique_models = []
        for model in models:
            model_id = model.get("id", "")
            if model_id and model_id not in seen_models:
                seen_models.add(model_id)
                unique_models.append(model)
        
        # Limiter à 15 modèles
        models_to_show = unique_models[:15]
        
        if not models_to_show:
            await context.bot.send_message(
                chat_id=chat_id,
                text="[WARN] Aucun modele disponible. Verifiez votre clé API.",
            )
            return
        
        keyboard = []
        for model in models_to_show:
            model_id = model.get("id", "")
            owned_by = model.get("owned_by", "")
            display_name = model_id
            if owned_by and owned_by != "unknown":
                display_name = f"{model_id} ({owned_by})"
            if model_id:
                keyboard.append([InlineKeyboardButton(display_name, callback_data=f"model_{model_id}")])
        
        keyboard.append([InlineKeyboardButton("Annuler", callback_data="cancel")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Afficher le modele actuel si deja selectionne
        if current_model:
            text = f"*[SELECT] Modele actuel: {current_model}*\n\nSelectionnez un autre modele pour NEMAPI :"
        else:
            text = "*[SELECT] Selectionnez un modele NEMAPI :*"
        
        await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
        )
        
        self.chat_states[chat_id] = AWAITING_MODEL_SELECTION

    async def new_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Gere la commande /new."""
        chat_id = update.effective_chat.id
        config = self._get_chat_config(chat_id)
        
        if self._needs_provider_config(chat_id):
            await context.bot.send_message(
                chat_id=chat_id,
                text="[ERROR] Configurez d'abord un provider avec /provider",
            )
            return
        
        if self._needs_model_selection(chat_id):
            await context.bot.send_message(
                chat_id=chat_id,
                text="[ERROR] Selectionnez d'abord un modele avec /model",
            )
            return
        
        provider = self._get_provider(chat_id)
        session = None
        try:
            from web.backend.sessions import create_session, save_session

            session = create_session(
                title="Conversation Telegram",
                model=config.get("nemapi", {}).get("model", ""),
            )
            session["telegram_chat_id"] = str(chat_id)
            save_session(session)
            config["session_id"] = session["id"]
            self._save_chat_config(chat_id, config)
        except Exception as exc:
            logger.warning("Impossible de créer une session Telegram persistante: %s", exc)
        if provider:
            provider.reset_conversation()
            if session and hasattr(provider, "configure_session"):
                provider.configure_session(session["id"], initialized=False)
        
        self.prompt_system_sent[chat_id] = False
        
        await context.bot.send_message(
            chat_id=chat_id,
            text="*[NEW] Nouvelle conversation demarree!*\n\n"
                 "Votre conversation est prete. Envoyez un message pour commencer.",
        )

    async def clear_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Gere la commande /clear."""
        chat_id = update.effective_chat.id
        
        provider = self._get_provider(chat_id)
        if provider:
            provider.reset_conversation()
            if hasattr(provider, "configure_session"):
                provider.configure_session(None, initialized=False)
        config = self._get_chat_config(chat_id)
        config.pop("session_id", None)
        self._save_chat_config(chat_id, config)
        self.prompt_system_sent[chat_id] = False
        
        await context.bot.send_message(
            chat_id=chat_id,
            text="*[CLEAR] Conversation effacee!*\n\n"
                 "La conversation a ete reinitialisee.",
        )

    async def config_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Gere la commande /config."""
        chat_id = update.effective_chat.id
        
        config = self._get_chat_config(chat_id)
        provider_type = "nemapi"
        
        config_text = (
            "*[CONFIG] Configuration actuelle*\n\n"
            f"Provider: {PROVIDER_LABELS.get(provider_type, provider_type)}\n"
        )
        
        provider_config = config.get("nemapi", {})
        config_text += f"Modele: {provider_config.get('model') or 'Non selectionne'}\n"
        config_text += f"Endpoint: {provider_config.get('host', '127.0.0.1')}:{provider_config.get('port', 8080)}\n"
        
        config_text += f"\nWorkspace: {config.get('security', {}).get('workspace', self.workspace)}"
        
        await context.bot.send_message(
            chat_id=chat_id,
            text=config_text,
        )

    async def tools_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Gere la commande /tools."""
        chat_id = update.effective_chat.id
        
        if not self.registry:
            self._init_nemesis()
        
        tools_list = list(self.registry._tools.keys())
        lines = ["[TOOLS] *Outils NEMESIS disponibles:*", ""]
        
        for i, tool in enumerate(tools_list, 1):
            tool_def = self.registry._tools[tool].definition
            description = (tool_def.description or "").split("\n")[0][:100]
            lines.append(f"{i}. `{tool}` - {description}")
        
        lines.append("")
        lines.append("💡 Utilisation:")
        lines.append("Envoyez: /read: fichier.txt")
        lines.append("Ou: {\"tool\": \"read_file\", \"parameters\": {\"path\": \"fichier.txt\"}}")
        
        for i in range(0, len(lines), 40):
            await context.bot.send_message(
                chat_id=chat_id,
                text='\n'.join(lines[i:i+40])
            )

    async def about_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Gere la commande /about."""
        chat_id = update.effective_chat.id
        
        about_text = (
            "[INFO] *A propos de NEMESIS Telegram Bot*\n\n"
            "NEMESIS-CLI - Agent IA autonome\n\n"
            "*Fonctionnalites :*\n"
            "- Execution de commandes systeme\n"
            "- Lecture/Ecriture de fichiers\n"
            "- Recherche web\n"
            "- Utilisation d'outils MCP\n"
            "- Support multi-providers\n"
            "- Gestion des conversations\n\n"
            "*Providers:*\n"
            ", ".join([f"{label}" for label in PROVIDER_LABELS.values()])
        )
        
        await context.bot.send_message(
            chat_id=chat_id,
            text=about_text,
        )

    async def history_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Gere la commande /history."""
        chat_id = update.effective_chat.id
        
        keyboard = [
            [InlineKeyboardButton("Effacer conversation", callback_data="history_clear")],
            [InlineKeyboardButton("Annuler", callback_data="cancel")],
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await context.bot.send_message(
            chat_id=chat_id,
            text="📜 *Gestion de l'historique* :\n\n"
                 "Choisissez une action.",
            reply_markup=reply_markup,
        )


    # =============================================================================
    # GESTION DES MESSAGES
    # =============================================================================

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Gere les messages texte."""
        chat_id = update.effective_chat.id
        incoming = update.message
        message_text = incoming.text or incoming.caption or ""
        attachment_candidates = self._telegram_attachment_candidates(incoming)
        has_attachments = bool(attachment_candidates)
        has_user_text = bool(message_text.strip())

        if not has_user_text and not has_attachments:
            return
        self._chat_message_counts[chat_id] = self._chat_message_counts.get(chat_id, 0) + 1
        if not message_text:
            message_text = "Lis le ou les fichiers joints et réponds en fonction de leur contenu."
        
        config = self._get_chat_config(chat_id)
        
        # Gestion des etats de configuration
        if chat_id in self.chat_states:
            state = self.chat_states[chat_id]
            
            if state == AWAITING_BRIDGE_HOST:
                if ":" in message_text:
                    parts = message_text.split(":", 1)
                    host = parts[0].strip()
                    try:
                        port = int(parts[1].strip())
                        config["bridge"] = {"host": host, "port": port}
                        config["provider"]["type"] = "bridge"
                        self._save_chat_config(chat_id, config)
                        
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=f"[OK] Bridge configure: {host}:{port}"
                        )
                        self.chat_states.pop(chat_id, None)
                        return
                    except ValueError:
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text="[ERROR] Port invalide. Format: host:port (ex: 192.168.1.67:8080)"
                        )
                        return
                else:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text="[ERROR] Format invalide. Utilisez: host:port"
                    )
                    return
            
            elif state == AWAITING_NEMAPI_HOST:
                if ":" in message_text:
                    parts = message_text.split(":", 1)
                    host = parts[0].strip()
                    try:
                        port = int(parts[1].strip())
                        config["nemapi"]["host"] = host
                        config["nemapi"]["port"] = port
                        config["provider"] = {"type": "nemapi"}
                        self._save_chat_config(chat_id, config)
                        self.providers.pop(chat_id, None)
                        
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=f"[OK] NEMAPI configure: {host}:{port}\nUtilisez /model pour choisir un modèle."
                        )
                        self.chat_states.pop(chat_id, None)
                        return
                    except ValueError:
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text="[ERROR] Port invalide. Format: host:port (ex: 127.0.0.1:8080)"
                        )
                        return
                else:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text="[ERROR] Format invalide. Utilisez: host:port"
                    )
                    return
            
            elif state == AWAITING_PROVIDER_CONFIG:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="[ERROR] NEMAPI n'utilise pas de clé API ici. Utilisez host:port."
                )
                self.chat_states.pop(chat_id, None)
                return

        attached_files, attached_images, attachment_errors = await self._collect_telegram_attachments(
            update, context, chat_id, config
        )
        if attachment_errors:
            await self._send_long_message(
                context,
                chat_id,
                "Certains fichiers joints n'ont pas pu être téléchargés :\n- "
                + "\n- ".join(attachment_errors),
            )
            if not attached_files and not has_user_text:
                return
        if attached_files:
            message_text = self._prepend_attachment_instruction(
                message_text, attached_files, attachment_errors
            )
        
        # Verifier si c'est une commande directe
        if message_text.startswith('/'):
            command = message_text[1:].split(' ')[0].split('@')[0]
            
            if command in ['start', 'help', 'provider', 'model', 'new', 'clear', 'config', 'tools', 'history', 'about']:
                return
            
            # Vérifier si c'est un outil direct
            tool_result = await self._handle_message_with_tools(message_text, chat_id, context, update)
            if tool_result is not None:
                await context.bot.send_message(chat_id=chat_id, text=tool_result)
                return
        
        # Vérifier si le provider est configuré
        if self._needs_provider_config(chat_id):
            await context.bot.send_message(
                chat_id=chat_id,
                text="[ERROR] Provider non configure\n\n"
                     "Utilisez /provider pour configurer.",
            )
            return
        
        # Vérifier si le modele est selectionne
        if self._needs_model_selection(chat_id):
            await context.bot.send_message(
                chat_id=chat_id,
                text="[ERROR] Modele non selectionne\n\n"
                     "Utilisez /model pour selectionner un modele.",
            )
            return
        
        # Verifier si c'est un appel d'outil direct
        tool_result = await self._handle_message_with_tools(message_text, chat_id, context, update)
        if tool_result is not None:
            await context.bot.send_message(chat_id=chat_id, text=tool_result)
            return
        
        # L'instruction sur les fichiers est préfixée au message utilisateur; les
        # images passent en parallèle par le canal multimodal data: base64.
        await self._process_ai_request(update, context, message_text, chat_id, images=attached_images)


    # =============================================================================
    # OUTILS
    # =============================================================================

    async def _execute_direct_tool(
        self, context: ContextTypes.DEFAULT_TYPE,
        tool_name: str, params: Dict[str, Any], chat_id: int,
    ) -> str:
        """Execute a direct Telegram tool request without an approval prompt."""
        if not isinstance(tool_name, str) or not isinstance(params, dict):
            return "Appel d'outil invalide : le nom doit être une chaîne et les paramètres un objet."
        aliases = {"read": "read_file", "write": "write_file", "append_file": "append", "replace": "edit"}
        canonical_name = aliases.get(tool_name, tool_name)
        result = await self._execute_tool(canonical_name, params, chat_id, context)
        return self._format_tool_result_for_display(result)

    async def _handle_message_with_tools(
        self, message: str, chat_id: int, context: ContextTypes.DEFAULT_TYPE,
        update: Optional[Update] = None,
    ) -> Optional[str]:
        """Traite un message qui peut contenir des appels d'outils."""
        message_stripped = message.strip()
        
        # Outils directs avec prefixe
        tool_prefixes = {
            'bash:': ('bash', lambda m: {'command': m[5:].strip()}),
            'read:': ('read_file', lambda m: {'path': m[5:].strip()}),
            'write:': ('write_file', lambda m: self._parse_write(m)),
            'grep:': ('grep', lambda m: {'pattern': m[5:].strip()}),
            'list:': ('list_dir', lambda m: {'path': m[5:].strip() or None}),
            'search:': ('web_search', lambda m: {'query': m[7:].strip()}),
            'fetch:': ('web_fetch', lambda m: self._parse_fetch(m)),
        }
        
        for prefix, (tool_name, parser) in tool_prefixes.items():
            if message_stripped.lower().startswith(prefix):
                params = parser(message_stripped)
                return await self._execute_direct_tool(context, tool_name, params, chat_id)
        
        # Détection des appels d'outils au format JSON avec code fence
        # Format: ```
        # {
        #   "tool": "nom_outil",
        #   "parameters": { ... }
        # }
        # ```
        import re
        json_block_pattern = r'```(?:)?\s*(\{[^`]*\})\s*```'
        matches = re.findall(json_block_pattern, message_stripped, re.DOTALL)
        
        for json_str in matches:
            try:
                tool_call = json.loads(json_str.strip())
                if 'tool' in tool_call and 'parameters' in tool_call:
                    tool_name = tool_call['tool']
                    params = tool_call['parameters']
                    return await self._execute_direct_tool(context, tool_name, params, chat_id)
            except json.JSONDecodeError:
                continue
        
        # JSON tool call simple (sans code fence)
        if message_stripped.startswith('{') and message_stripped.endswith('}'):
            try:
                tool_call = json.loads(message_stripped)
                if 'tool' in tool_call and 'parameters' in tool_call:
                    return await self._execute_direct_tool(
                        context, tool_call['tool'], tool_call['parameters'], chat_id
                    )
            except json.JSONDecodeError:
                pass
        
        return None
    
    def _parse_write(self, message: str) -> Dict[str, Any]:
        """Parse un message write:"""
        parts = message[6:].strip().split('|', 1)
        if len(parts) >= 2:
            return {'file_path': parts[0].strip(), 'content': parts[1].strip()}
        return {'file_path': '', 'content': ''}
    
    def _parse_fetch(self, message: str) -> Dict[str, Any]:
        """Parse un message fetch:"""
        parts = message[6:].strip().split('|', 1)
        url = parts[0].strip()
        fmt = parts[1].strip() if len(parts) > 1 else 'markdown'
        return {'url': url, 'format': fmt}

    async def _execute_tool(self, tool_name: str, parameters: Dict[str, Any],
                          chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> Dict[str, Any]:
        """Execute un outil et retourne un dict avec success/content/error."""
        try:
            if not self.registry:
                self._init_nemesis()

            if tool_name not in self.registry._tools:
                alt_names = {
                    'read': 'read_file',
                    'write': 'write_file',
                    'append_file': 'append',
                    'replace': 'edit',
                }
                tool_name = alt_names.get(tool_name, tool_name)

            if tool_name not in self.registry._tools:
                available_tools = list(self.registry._tools.keys())
                return {"success": False, "error": f"Outil inconnu: {tool_name}"}

            parameters = self._normalize_parameters(tool_name, parameters)
            self.executor.set_scheduler_context(f"telegram:{chat_id}", str(chat_id))
            tool_updates = list(self.executor.execute_tool(tool_name, parameters))
            result = tool_updates[-1] if tool_updates else {
                "success": False,
                "error": "L'outil n'a retourné aucun résultat.",
            }
            if tool_name == "send_file_to_user" and isinstance(result, dict) and result.get("success"):
                file_path = result.get("telegram_file_path")
                file_name = result.get("telegram_file_name") or Path(file_path).name
                caption = result.get("telegram_caption") or None
                with open(file_path, "rb") as file_handle:
                    await context.bot.send_document(
                        chat_id=chat_id,
                        document=file_handle,
                        filename=file_name,
                        caption=caption,
                    )
                sent_message = f"Document envoyé dans ce chat : {file_name}."
                return {
                    "success": True,
                    "content": sent_message,
                    "output": sent_message,
                    "error": None,
                }
            
            # Formater le résultat
            formatted = self._format_tool_result(result)
            
            # Déterminer le succès
            if isinstance(result, dict):
                success = result.get('success', True)
                error = result.get('error', None)
                content = result.get('content', result.get('output', formatted))
            else:
                success = True
                error = None
                content = formatted
            
            result_dict = {
                "success": success,
                "content": content,
                "error": error,
                "output": content  # Pour compatibilité
            }
            if isinstance(result, dict) and result.get("images"):
                result_dict["images"] = result["images"]
            return result_dict
            
        except Exception as e:
            logger.error(f"Erreur outil {tool_name}: {e}")
            return {"success": False, "error": str(e), "content": "", "output": ""}

    def _telegram_attachment_candidates(self, incoming) -> List[Tuple[Any, str, str]]:
        """Collect all Telegram media types that can be downloaded as files."""
        candidates: List[Tuple[Any, str, str]] = []
        for attr, fallback_name, fallback_mime in (
            ("document", "document", "application/octet-stream"),
            ("video", "video.mp4", "video/mp4"),
            ("audio", "audio", "audio/mpeg"),
            ("voice", "voice.ogg", "audio/ogg"),
            ("animation", "animation.mp4", "video/mp4"),
            ("video_note", "video_note.mp4", "video/mp4"),
        ):
            item = getattr(incoming, attr, None)
            if item:
                name = getattr(item, "file_name", None) or fallback_name
                mime = getattr(item, "mime_type", None) or fallback_mime
                candidates.append((item, str(mime), str(name)))
        photo_sizes = getattr(incoming, "photo", None)
        if photo_sizes:
            largest = max(
                photo_sizes,
                key=lambda item: (
                    int(getattr(item, "file_size", 0) or 0),
                    int(getattr(item, "width", 0) or 0) * int(getattr(item, "height", 0) or 0),
                ),
            )
            candidates.append((largest, "image/jpeg", "photo.jpg"))
        return candidates

    def _telegram_upload_directory(self, chat_id: int, config: Dict[str, Any]) -> Path:
        """Prefer the configured workspace, falling back to the system temp dir."""
        raw_workspace = str(config.get("security", {}).get("workspace") or "").strip()
        candidates = []
        if raw_workspace:
            workspace = Path(raw_workspace).expanduser()
            if workspace.is_dir() and os.access(workspace, os.W_OK):
                candidates.append(workspace / ".nemesis" / "telegram_uploads" / str(chat_id))
        candidates.append(Path(tempfile.gettempdir()) / "nemesis-telegram-uploads" / str(chat_id))
        last_error = None
        for upload_dir in candidates:
            try:
                upload_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
                try:
                    upload_dir.chmod(0o700)
                except OSError:
                    pass
                return upload_dir
            except OSError as exc:
                last_error = exc
        raise OSError(f"Impossible de créer le dossier de réception : {last_error}")

    def _prepend_attachment_instruction(
        self, message: str, files: List[Dict[str, Any]], errors: Optional[List[str]] = None
    ) -> str:
        lines = [
            "FICHIERS JOINTS REÇUS VIA TELEGRAM",
            "L'utilisateur a envoyé les fichiers suivants. Lis les fichiers non-image avec read_file (ou un outil adapté au format) avant de répondre.",
        ]
        for item in files:
            path_literal = json.dumps(item["path"], ensure_ascii=False)
            lines.append(
                f"- Nom : {item['name']} | Type : {item['mime']} | Taille : {item['size']} octets | Chemin local (JSON) : {path_literal}"
            )
        if any(item.get("is_image") for item in files):
            lines.append(
                "Les images reconnues sont aussi jointes à ce même tour en multimodal; examine-les visuellement, sans les ouvrir comme du texte ni afficher leur base64."
            )
        if errors:
            lines.append("Fichiers non reçus : " + "; ".join(errors))
        lines.extend(["", "MESSAGE DE L'UTILISATEUR", message])
        return "\n".join(lines)

    async def _collect_telegram_attachments(
        self, update: Update, context, chat_id: int, config: Dict[str, Any]
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[str]]:
        """Download documents/media to workspace or tmp and make image data URLs on send."""
        from src.core.image_attachments import detect_image_mime

        incoming = getattr(update, "message", None)
        if incoming is None:
            return [], [], []
        candidates = self._telegram_attachment_candidates(incoming)
        saved_files: List[Dict[str, Any]] = []
        images: List[Dict[str, Any]] = []
        errors: List[str] = []
        if not candidates:
            return saved_files, images, errors
        eligible = []
        for file_obj, mime, original_name in candidates:
            name = Path(str(original_name)).name
            safe_name = re.sub(r"[^\w.-]+", "_", name, flags=re.UNICODE).strip("._")[:120]
            safe_name = safe_name or "telegram_attachment"
            announced_size = int(getattr(file_obj, "file_size", 0) or 0)
            if announced_size > MAX_TELEGRAM_DOWNLOAD_BYTES:
                errors.append(f"{safe_name} dépasse la limite de téléchargement Telegram (20 Mio).")
                continue
            eligible.append((file_obj, mime, safe_name, announced_size))
        if not eligible:
            return saved_files, images, errors
        try:
            upload_dir = self._telegram_upload_directory(chat_id, config)
        except OSError as exc:
            errors.append(f"Impossible de préparer le dossier de réception : {exc}")
            return saved_files, images, errors

        for file_obj, mime, safe_name, announced_size in eligible:
            target = upload_dir / f"{uuid.uuid4().hex[:12]}_{safe_name}"
            try:
                telegram_file = await context.bot.get_file(file_obj.file_id)
                remote_size = int(getattr(telegram_file, "file_size", 0) or announced_size)
                if remote_size > MAX_TELEGRAM_DOWNLOAD_BYTES:
                    errors.append(f"{safe_name} dépasse la limite de téléchargement Telegram (20 Mio).")
                    continue
                await telegram_file.download_to_drive(custom_path=str(target))
                actual_size = target.stat().st_size
                if actual_size > MAX_TELEGRAM_DOWNLOAD_BYTES:
                    try:
                        target.unlink(missing_ok=True)
                    except OSError:
                        pass
                    errors.append(f"{safe_name} dépasse la limite de téléchargement Telegram (20 Mio).")
                    continue
                try:
                    target.chmod(0o600)
                except OSError:
                    pass
                image_mime = detect_image_mime(str(target))
                safe_mime = re.sub(r"[^A-Za-z0-9.+_/-]", "", mime)[:80] or "application/octet-stream"
                record = {
                    "name": safe_name,
                    "mime": image_mime or safe_mime,
                    "path": str(target.resolve()),
                    "size": actual_size,
                    "is_image": bool(image_mime),
                }
                saved_files.append(record)
                if image_mime:
                    images.append({"path": record["path"], "mime": image_mime, "name": safe_name})
            except Exception as exc:  # noqa: BLE001 - continue with other attachments
                try:
                    target.unlink(missing_ok=True)
                except OSError:
                    pass
                logger.warning("Téléchargement Telegram de %s échoué: %s", safe_name, exc)
                errors.append(f"{safe_name} : {str(exc)[:240]}")
        return saved_files, images, errors

    def _run_scheduled_task(self, task: dict) -> dict:
        """Run a scheduled task for its originating Telegram chat and notify it."""
        from src.core.scheduler import schedule_agent_task

        target = task.get("target_id")
        if target is None:
            return {"success": False, "error": "Chat Telegram absent pour cette tâche."}
        chat_id = int(target)
        config = self._get_chat_config(chat_id)
        result = schedule_agent_task(
            config, task["instruction"], f"telegram:{chat_id}", str(chat_id)
        )
        final_text = result.get("text") or result.get("error") or "La tâche planifiée est terminée."
        text = self._format_ai_response([f"Tâche planifiée — {task['task_id']}\n\n{final_text}"], "nemapi", config.get("nemapi", {}).get("model", ""))
        loop = self._event_loop
        bot = self.app.bot if self.app is not None else self.bot
        if loop is not None and loop.is_running() and bot is not None:
            future = asyncio.run_coroutine_threadsafe(
                self._send_long_message(SimpleNamespace(bot=bot), chat_id, text), loop
            )
            future.add_done_callback(
                lambda done: logger.error("Envoi de notification de tâche planifiée échoué: %s", done.exception())
                if done.exception() else None
            )
        return result

    async def _process_ai_request(self, update: Update, context: ContextTypes.DEFAULT_TYPE, 
                                  message: str, chat_id: int, images: Optional[List[Any]] = None):
        """Traite une requete pour l'IA - version simplifiée sans autorisations."""
        self._event_loop = asyncio.get_running_loop()
        config = self._get_chat_config(chat_id)
        provider_type = "nemapi"
        model = config.get("nemapi", {}).get("model", "")
        
        provider = self._get_provider(chat_id)
        if not provider:
            await context.bot.send_message(chat_id=chat_id, text="[ERROR] Provider non disponible")
            return
        
        # Build the system prompt and merge it into the first user message so a
        # fresh chat costs a single provider round-trip instead of two.
        system_prefix = ""
        if not self.prompt_system_sent.get(chat_id, False):
            system_prefix = self._build_system_prompt_text(chat_id)
            self.prompt_system_sent[chat_id] = True
        session_id = config.get("session_id")
        mark_session_initialized = bool(session_id and system_prefix)
        if session_id:
            try:
                from web.backend.sessions import append_message, get_session, set_title

                append_message(session_id, {"kind": "user", "text": message})
                session = get_session(session_id)
                if session and session.get("title") == "Conversation Telegram":
                    set_title(session_id, message[:72] or "Conversation Telegram")
            except Exception as exc:
                logger.debug("Impossible d'archiver le message Telegram : %s", exc)

        # Afficher un message de statut
        status_msg = None
        try:
            status_msg = await context.bot.send_message(
                chat_id=chat_id,
                text="⏳ NEMESIS traite votre demande..."
            )
        except Exception:
            pass
        
        try:
            # Boucle de traitement avec l'IA (autorisations désactivées)
            current_input = message
            current_role = "user"
            pending_images: List[Any] = list(images or [])
            iteration = 0
            last_ai_text = ""  # Stocke le dernier message de l'IA
            feedback_seq = 0

            # No iteration ceiling: the loop ends when the model stops calling
            # tools, when the provider fails, or when the user interrupts.
            while True:
                iteration += 1
                
                # Envoyer à l'IA
                from src.core.image_attachments import send_message_with_images

                result = send_message_with_images(
                    provider,
                    current_input,
                    role=current_role,
                    images=pending_images,
                    system_prefix=system_prefix,
                )
                pending_images = []
                system_prefix = ""
                
                if not result or not isinstance(result, dict):
                    await self._cleanup_thinking_message(context, chat_id, status_msg)
                    await context.bot.send_message(chat_id=chat_id, text="[ERROR] Réponse invalide")
                    return
                
                if not result.get("success"):
                    await self._cleanup_thinking_message(context, chat_id, status_msg)
                    error = result.get('error', 'Erreur inconnue')
                    await context.bot.send_message(chat_id=chat_id, text=f"[ERROR] {error}")
                    return
                if mark_session_initialized and session_id:
                    try:
                        from web.backend.sessions import mark_initialized

                        mark_initialized(session_id)
                    except Exception as exc:
                        logger.debug("Impossible de marquer la session comme initialisée : %s", exc)
                    mark_session_initialized = False
                
                # Parser la réponse
                raw_response = result.get('response', '')
                parsed = self.parser.parse(raw_response)

                protocol_error = parsed.get("protocol_error")
                if protocol_error:
                    from src.core.agent_tools import protocol_error_feedback

                    feedback_seq += 1
                    current_input = protocol_error_feedback(protocol_error, feedback_seq)
                    current_role = "tool_result"
                    continue

                # Extraire le texte de l'IA (filtrer les blocs JSON d'appels d'outils)
                display_text = parsed.get('text', '')
                action = parsed.get('action')
                
                # Ne stocker que le dernier message de l'IA qui n'est pas un feedback
                if display_text and not display_text.startswith("FEEDBACK:"):
                    # Nettoyer les blocs JSON d'appels d'outils
                    import re
                    clean_text = re.sub(r'```\s*\{[^`]*\}\s*```', '', display_text, flags=re.DOTALL)
                    clean_text = re.sub(r'```\s*\{[^`]*\}\s*```', '', clean_text, flags=re.DOTALL)
                    clean_text = re.sub(r'\{\s*"tool"\s*:.*?\}', '', clean_text, flags=re.DOTALL)
                    clean_text = clean_text.strip()
                    if clean_text:
                        last_ai_text = clean_text
                
                # Execute every call in the model turn, preserving order.
                actions = parsed.get("actions") or ([action] if action else [])
                if not actions:
                    break
                feedback_parts = []
                for action_item in actions:
                    act_type = action_item.get('type', '')
                    act_content = action_item.get('content', {})
                    tool_result = await self._execute_tool(act_type, act_content, chat_id, context)
                    from src.core.agent_tools import format_tool_feedback
                    from src.core.image_attachments import collect_result_images

                    pending_images.extend(collect_result_images(tool_result))
                    if isinstance(tool_result, dict):
                        feedback_parts.append(format_tool_feedback(act_type, tool_result))
                    else:
                        feedback_parts.append(
                            format_tool_feedback(act_type, {"success": True, "stdout": str(tool_result)})
                        )

                from src.core.agent_tools import build_feedback_message

                feedback_seq += 1
                current_input = build_feedback_message(feedback_parts, feedback_seq)
                current_role = "tool_result"
            
            # Supprimer le message de statut
            await self._cleanup_thinking_message(context, chat_id, status_msg)
            
            # Envoyer la réponse finale (seul le dernier message)
            if last_ai_text:
                formatted_response = self._format_ai_response([last_ai_text], provider_type, model)
                if session_id:
                    try:
                        from web.backend.sessions import append_message

                        append_message(session_id, {"kind": "assistant", "text": last_ai_text})
                    except Exception as exc:
                        logger.debug("Impossible d'archiver la réponse Telegram : %s", exc)
                # Découper si le message est trop long
                await self._send_long_message(context, chat_id, formatted_response)
            else:
                await context.bot.send_message(chat_id=chat_id, text="[INFO] Aucune réponse textuelle générée.")
            
        except Exception as e:
            await self._cleanup_thinking_message(context, chat_id, status_msg)
            logger.error(f"Erreur dans _process_ai_request: {e}")
            await context.bot.send_message(chat_id=chat_id, text=f"[ERROR] Erreur: {str(e)[:1000]}")

    async def _cleanup_thinking_message(self, context: ContextTypes.DEFAULT_TYPE, chat_id: int, thinking_msg):
        """Supprime le message 'réfléchit' si possible."""
        if thinking_msg:
            try:
                await context.bot.delete_message(
                    chat_id=chat_id,
                    message_id=thinking_msg.message_id
                )
            except Exception:
                pass

    def _build_system_prompt_text(self, chat_id: int) -> str:
        """Construit le prompt système depuis le registre d'outils vivant.

        Le prompt (catalogue d'outils + mémoire) est fusionné avec le premier
        message utilisateur du tour afin de ne consommer qu'un seul aller-retour
        provider au démarrage d'une conversation.
        """
        if not self.registry:
            self._init_nemesis()
        try:
            from src.core.agent_tools import build_system_prompt
            return (build_system_prompt(self.registry) or "") + TELEGRAM_SYSTEM_PROMPT_EXTENSION
        except Exception as e:
            logger.error(f"Erreur construction prompt système: {e}")
            return "You are NEMESIS. Use only the tools provided by the runtime.\n" + TELEGRAM_SYSTEM_PROMPT_EXTENSION

    def _format_ai_response(self, outputs: List[str], provider_type: str, model: str) -> str:
        """Convertit uniquement le texte final Markdown en texte clair structuré."""
        combined_text = markdown_to_plain_text("\n\n".join(outputs))
        header = f"NEMESIS — {provider_type} ({model})" if model else f"NEMESIS — {provider_type}"
        return f"{header}\n\n{combined_text}".strip()
    
    async def _send_long_message(self, context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str):
        """Envoie du texte brut, découpé sans dépasser la limite Telegram."""
        max_length = 3850  # Reserve room for the [Partie x/y] prefix.
        parts: List[str] = []
        current = ""
        for line in str(text).splitlines():
            pieces = [line[i:i + max_length] for i in range(0, len(line), max_length)] or [""]
            for piece in pieces:
                candidate = f"{current}\n{piece}" if current else piece
                if len(candidate) > max_length and current:
                    parts.append(current)
                    current = piece
                else:
                    current = candidate
        if current or not parts:
            parts.append(current)

        for index, part in enumerate(parts, 1):
            if len(parts) > 1:
                part = f"Partie {index}/{len(parts)}\n\n{part}"
            await context.bot.send_message(chat_id=chat_id, text=part)

    def _tool_result_text(self, result: Dict[str, Any]) -> str:
        """Texte d'un résultat d'outil: sortie, stderr et code de sortie."""
        stdout = (
            result.get("stdout")
            or result.get("output")
            or result.get("content")
            or ""
        )
        stderr = result.get("error") or result.get("stderr") or ""
        exit_code = result.get("exit_code", result.get("returncode"))
        parts = []
        text = str(stdout)
        if text:
            parts.append(text)
        if stderr and str(stderr) not in text:
            parts.append(f"[stderr] {stderr}")
        if exit_code is not None and f"exit code: {exit_code}" not in text:
            parts.append(f"[exit {exit_code}]")
        block = "\n".join(parts) or "[aucune sortie]"
        if len(block) > 10000:
            block = block[:10000] + "\n\n... (tronqué)"
        return block

    def _format_tool_result_for_display(self, tool_result: Dict[str, Any]) -> str:
        """Formate un résultat d'outil (dict) en chaîne pour affichage."""
        if isinstance(tool_result, dict):
            body = self._tool_result_text(tool_result)
            if not tool_result.get("success", True):
                return f"[ERROR] Erreur outil:\n{body}"
            return body
        return str(tool_result)

    def _normalize_parameters(self, tool_name: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Adapte les parametres."""
        parameters = dict(parameters)
        
        if tool_name == "read_file":
            if "files" in parameters and "paths" not in parameters and "path" not in parameters:
                parameters["paths"] = parameters.pop("files")
        elif tool_name == "bash":
            parameters.pop("mode", None)
        elif tool_name == "edit":
            if "path" in parameters and "file_path" not in parameters:
                parameters["file_path"] = parameters.pop("path")
            if "blocks" in parameters and isinstance(parameters["blocks"], list):
                blocks = parameters["blocks"]
                if len(blocks) == 1:
                    parameters["old_string"] = blocks[0].get("search", "")
                    parameters["new_string"] = blocks[0].get("replace", "")
                parameters.pop("blocks", None)
        
        return parameters

    def _format_tool_result(self, result) -> str:
        """Formate le resultat d'un outil."""
        if hasattr(result, '__iter__') and not isinstance(result, (str, dict)):
            output_parts = []
            for r in result:
                output_parts.append(self._format_tool_result(r))
            return '\n'.join(output_parts)
        
        if isinstance(result, dict):
            body = self._tool_result_text(result)
            if result.get('success'):
                return body
            return f"[ERROR] Erreur:\n{body}"
        
        if isinstance(result, str):
            if len(result) > 10000:  # 10 Ko
                return result[:10000] + "\n\n... (tronque)"
            return result
        
        return str(result)


    # =============================================================================
    # GESTION DES CALLBACKS
    # =============================================================================

    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Gere les callbacks des boutons inline."""
        query = update.callback_query
        await query.answer()
        
        chat_id = query.message.chat_id
        data = query.data
        config = self._get_chat_config(chat_id)
        
        if data == "cancel":
            await query.edit_message_text("[ERROR] Operation annulee.")
            self.chat_states.pop(chat_id, None)
        
        elif data.startswith("provider_"):
            provider_type = data[9:]
            if provider_type != "nemapi":
                await query.edit_message_text("[ERROR] NEMAPI est le seul provider disponible.")
                return
            config["provider"]["type"] = provider_type
            config.setdefault("nemapi", {})
            self._save_chat_config(chat_id, config)
            
            await query.edit_message_text(
                "Configuration NEMAPI\n\n"
                "Envoyez l'endpoint sous la forme host:port "
                "(exemple : 127.0.0.1:8080)."
            )
            self.chat_states[chat_id] = AWAITING_NEMAPI_HOST
        
        elif data.startswith("model_"):
            model_id = data[6:]
            config.setdefault("nemapi", {})["model"] = model_id
            self._save_chat_config(chat_id, config)
            provider = self.providers.get(chat_id)
            if provider is not None:
                provider.model = model_id
            
            await query.edit_message_text(f"[OK] Modele selectionne: {model_id}")
            await context.bot.send_message(
                chat_id=chat_id,
                text="[NEW] Utilisez /new pour demarrer une conversation.",
            )
            self.chat_states.pop(chat_id, None)
        
        elif data in {"confirm_yes", "confirm_no", "tool_yes", "tool_no", "tool_always"} or data.startswith(
            ("confirm_tool_", "cancel_tool_")
        ):
            await query.edit_message_text(
                "Ce bouton n'est plus actif. Envoyez à nouveau votre demande."
            )
        
        elif data == "history_clear":
            provider = self._get_provider(chat_id)
            if provider:
                provider.reset_conversation()
                if hasattr(provider, "configure_session"):
                    provider.configure_session(None, initialized=False)
            config.pop("session_id", None)
            self._save_chat_config(chat_id, config)
            self.prompt_system_sent[chat_id] = False
            await query.edit_message_text("[CLEAR] Conversation effacee.")


    # =============================================================================
    # DEMARRAGE
    # =============================================================================

    async def _post_init(self, application: Application) -> None:
        self._event_loop = asyncio.get_running_loop()
        self.bot = application.bot
        try:
            commands = [
                BotCommand(name, description[:256])
                for name, _original, description in self._telegram_command_specs()
            ]
            await application.bot.set_my_commands(commands)
        except Exception as exc:
            logger.warning("Impossible de synchroniser la liste des commandes Telegram : %s", exc)

    def run(self):
        """Demarre le bot."""
        if not self.token:
            raise ValueError("Token Telegram manquant!")
        
        self.app = (Application.builder()
            .token(self.token)
            .connect_timeout(30.0)
            .read_timeout(30.0)
            .write_timeout(30.0)
            .post_init(self._post_init)
            .build())
        
        # Ajouter les handlers
        self.app.add_handler(CommandHandler("start", self.start))
        self.app.add_handler(CommandHandler("help", self.help_command))
        self.app.add_handler(CommandHandler("provider", self.provider_command))
        self.app.add_handler(CommandHandler("model", self.model_command))
        self.app.add_handler(CommandHandler("new", self.new_command))
        self.app.add_handler(CommandHandler("clear", self.clear_command))
        self.app.add_handler(CommandHandler("config", self.config_command))
        self.app.add_handler(CommandHandler("tools", self.tools_command))
        self.app.add_handler(CommandHandler("history", self.history_command))
        self.app.add_handler(CommandHandler("about", self.about_command))
        native_handlers = {
            "start", "help", "provider", "model", "new", "clear", "config",
            "tools", "history", "about",
        }
        for command_name, _original, _description in self._telegram_command_specs():
            if command_name not in native_handlers:
                self.app.add_handler(CommandHandler(command_name, self.cli_command))
        attachment_filter = (
            filters.PHOTO
            | filters.Document.ALL
            | filters.VIDEO
            | filters.AUDIO
            | filters.VOICE
            | filters.VIDEO_NOTE
            | filters.ANIMATION
        )
        self.app.add_handler(
            MessageHandler((filters.TEXT | attachment_filter) & ~filters.COMMAND, self.handle_message)
        )
        self.app.add_handler(CallbackQueryHandler(self.button_handler))

        logger.info("Demarrage du bot Telegram NEMESIS-CLI...")
        self.app.run_polling(allowed_updates=Update.ALL_TYPES)

    def stop(self):
        """Arrete le bot."""
        if self.app:
            self.app.stop()


# =============================================================================
# FONCTIONS UTILITAIRES
# =============================================================================

def load_bot_config(config_path: str = "telegram_config.yaml") -> Dict[str, Any]:
    """Charge la configuration du bot depuis un fichier YAML.

    La configuration enregistree depuis l'UI web (sous le repertoire de config
    utilisateur partage) est fusionnee par dessus le fichier du projet, afin que
    le token du bot et les reglages soient communs aux deux interfaces.
    """
    import yaml
    config_path = Path(config_path)
    merged: Dict[str, Any] = {}
    if config_path.exists():
        with open(config_path, 'r') as f:
            loaded = yaml.safe_load(f)
            if isinstance(loaded, dict):
                merged.update(loaded)
    try:
        from src.core.paths import USER_CONFIG_DIR
        user_overlay = Path(USER_CONFIG_DIR) / "telegram_bot.yaml"
        if user_overlay.exists():
            with open(user_overlay, 'r') as f:
                loaded = yaml.safe_load(f)
                if isinstance(loaded, dict):
                    nemapi = merged.get("nemapi") if isinstance(merged.get("nemapi"), dict) else {}
                    if isinstance(loaded.get("nemapi"), dict):
                        nemapi.update(loaded["nemapi"])
                    merged.update(loaded)
                    if nemapi:
                        merged["nemapi"] = nemapi
    except Exception:
        pass
    return merged


def _prompt_token_interactive() -> Optional[str]:
    """Ask for the bot token on first run and persist it in the user config dir."""
    if not sys.stdin.isatty():
        return None
    print("\n=== Configuration initiale du bot Telegram ===")
    print("Creez un bot via @BotFather puis collez le token ci-dessous.")
    try:
        token = input("Token du bot Telegram : ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None
    if not token:
        return None
    try:
        from src.core.paths import USER_CONFIG_DIR
        config_dir = Path(USER_CONFIG_DIR)
    except Exception:
        config_dir = Path.home() / ".config" / "nemesis-cli"
    try:
        config_dir.mkdir(parents=True, exist_ok=True)
        import yaml
        overlay = config_dir / "telegram_bot.yaml"
        data = {}
        if overlay.exists():
            with open(overlay, "r", encoding="utf-8") as f:
                loaded = yaml.safe_load(f)
                if isinstance(loaded, dict):
                    data = loaded
        data["token"] = token
        with open(overlay, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
        try:
            os.chmod(overlay, 0o600)
        except OSError:
            pass
        print(f"[OK] Token enregistre dans {overlay}")
    except Exception as exc:
        print(f"[AVERTISSEMENT] Impossible d'enregistrer le token : {exc}")
    return token


def main():
    """Point d'entree principal."""
    config = load_bot_config()
    token = os.getenv('TELEGRAM_BOT_TOKEN') or config.get('token')
    workspace = config.get('workspace') or os.getcwd()

    if not token:
        token = _prompt_token_interactive()
        if token:
            config['token'] = token

    if not token:
        print("[ERROR] Erreur: Token Telegram manquant!")
        print("   Definissez TELEGRAM_BOT_TOKEN ou lancez nemesis-bot en interactif")
        sys.exit(1)

    if config.get('workspace'):
        workspace = config['workspace']

    from src.core.paths import set_session_cwd

    set_session_cwd(workspace)

    bot = NemesisTelegramBot(token=token, workspace=workspace, default_config=config)
    
    try:
        bot.run()
    except KeyboardInterrupt:
        print("\nArret du bot...")
        bot.stop()
    except Exception as e:
        logger.error(f"Erreur fatale: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
