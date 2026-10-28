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
import time
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from types import SimpleNamespace

if getattr(sys, "frozen", False):
    script_dir = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
else:
    script_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(script_dir))

from telegram import Update, Message, Bot, InlineKeyboardButton, InlineKeyboardMarkup
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

# Etats de conversation
AWAITING_PROVIDER_CONFIG = 1
AWAITING_MODEL_SELECTION = 2
AWAITING_CONFIRMATION = 3
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
            normalized = {
                "provider": {"type": "nemapi"},
                "nemapi": {
                    "host": str(nemapi.get("host") or "127.0.0.1"),
                    "port": int(nemapi.get("port") or 8080),
                    "model": str(nemapi.get("model") or ""),
                },
                "security": {
                    "workspace": str(
                        (loaded.get("security") or {}).get("workspace")
                        or default_config["security"]["workspace"]
                    )
                },
            }
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
        self.parser = ActionParser(strict=True)
        self._event_loop = None
        from src.core.scheduler import get_scheduler

        get_scheduler().register_handler("telegram:*", self._run_scheduled_task)
        self.providers: Dict[int, Any] = {}
        self._session_authorized_tools: Dict[int, set] = {}
        self._feedback_sequences: Dict[int, int] = {}
        
        # Etats de conversation par chat
        self.chat_states: Dict[int, int] = {}
        
        # Donnees temporaires
        self.pending_config: Dict[int, Dict[str, Any]] = {}
        self.pending_actions: Dict[int, Dict[str, Any]] = {}
        
        # Suivi du prompt système envoyé par chat
        self.prompt_system_sent: Dict[int, bool] = {}
        
        # Initialiser NEMESIS
        self._init_nemesis()
        
        # S'assurer que le repertoire de config existe
        ensure_config_dir()

    def _init_nemesis(self):
        """Initialise les composants NEMESIS."""
        self.registry = self.executor.registry
        logger.info("NEMESIS agent initialized with %d tools", len(self.registry._tools))

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
        """Gere la commande /start."""
        chat_id = update.effective_chat.id
        user = update.effective_user
        
        welcome_message = (
            "*NEMESIS-CLI — agent autonome de codage*\n\n"
            f"Bonjour {user.first_name}!\n\n"
            "Je suis NEMESIS, votre agent IA autonome de codage et d'administration systeme.\n\n"
            "Provider actif : NEMAPI. Configurez son endpoint avec /provider puis "
            "choisissez un modele avec /model.\n\n"
            "*💡 Commandes principales :*\n"
            "/start - Demarrer le bot\n"
            "/help - Aide\n"
            "/provider - Configurer le provider LLM\n"
            "/model - Changer le modele\n"
            "/new - Nouvelle conversation\n"
            "/clear - Effacer la conversation\n"
            "/tools - Liste des outils disponibles\n"
            "/config - Afficher la configuration actuelle\n"
            "/history - Gerer l'historique\n\n"
            "*[TOOLS] Outils integrés :*\n"
            "read_file, write_file, append, bash, grep, list_dir, web_search, web_fetch\n"
            "schedule_task, list_scheduled_tasks, cancel_scheduled_task, wait\n"
            "mcp_list, mcp_tools_list, mcp_call, et bien plus!\n\n"
            "*[INFO] Les appels d'outils et les tours internes restent invisibles : "
            "je vous renvoie uniquement la synthese finale.*"
        )
        
        await context.bot.send_message(
            chat_id=chat_id,
            text=welcome_message,
        )
        
        if not self._needs_provider_config(chat_id):
            await context.bot.send_message(
                chat_id=chat_id,
                text="*[OK] Votre provider est deja configure.* Utilisez /new pour commencer une conversation.",
            )

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Gere la commande /help."""
        chat_id = update.effective_chat.id
        
        help_text = (
            "*[HELP] Aide NEMESIS-CLI Telegram*\n\n"
            "*[SELECT] Configuration :*\n"
            "/provider - Configurer le provider LLM\n"
            "/model - Changer le modele\n"
            "/config - Afficher la configuration\n\n"
            "*[CHAT] Conversation :*\n"
            "/new - Nouvelle conversation\n"
            "/clear - Effacer conversation\n"
            "/history - Gerer historique\n\n"
            "*📊 Informations :*\n"
            "/start - Menu de demarrage\n"
            "/help - Aide\n"
            "/tools - Liste des outils\n"
            "/about - A propos\n\n"
            "*[SETTINGS] Outils :*\n"
            "Envoyez: /read: fichier\n"
            "       /bash: commande\n"
            "       /grep: pattern\n"
            "       /search: requete\n"
            "       /fetch: url\n"
            "Planifiez une tâche avec l'agent ou demandez-lui de patienter.\n\n"
            "*[LOCK] Securite :*\n"
            "Les actions sensibles demandent confirmation."
        )
        
        await context.bot.send_message(
            chat_id=chat_id,
            text=help_text,
        )

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
        if provider:
            provider.reset_conversation()
        
        # Réinitialiser le flag de prompt système pour une nouvelle conversation
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
        document = getattr(incoming, "document", None)
        has_image = bool(getattr(incoming, "photo", None)) or bool(
            document and str(getattr(document, "mime_type", "") or "").startswith("image/")
        )

        if not message_text and not has_image:
            return
        if not message_text:
            message_text = "Décris et analyse cette image."
        
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
        
        # Verifier si c'est une commande directe
        if message_text.startswith('/'):
            command = message_text[1:].split(' ')[0].split('@')[0]
            
            if command in ['start', 'help', 'provider', 'model', 'new', 'clear', 'config', 'tools', 'history', 'about']:
                return
            
            # Vérifier si c'est un outil direct
            tool_result = await self._handle_message_with_tools(message_text, chat_id, context)
            if tool_result:
                if self._is_sensitive_tool(message_text):
                    await self._ask_for_confirmation(update, context, message_text)
                else:
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
        tool_result = await self._handle_message_with_tools(message_text, chat_id, context)
        if tool_result:
            # Déterminer le nom de l'outil depuis le message
            tool_name = self._extract_tool_name(message_text)
            if tool_name and self._is_sensitive_tool(tool_name):
                # Extraire les paramètres pour la confirmation
                params = self._extract_tool_params(message_text, tool_name)
                await self._ask_for_confirmation(update, context, tool_name, params)
            else:
                await context.bot.send_message(chat_id=chat_id, text=tool_result)
            return
        
        # Traiter comme une requete pour l'IA (photos → pièces jointes multimodales)
        attached_images = await self._collect_telegram_images(update, context)
        if attached_images and not message_text.strip():
            message_text = "Décris et analyse cette image."
        await self._process_ai_request(update, context, message_text, chat_id, images=attached_images)


    # =============================================================================
    # OUTILS
    # =============================================================================

    async def _handle_message_with_tools(self, message: str, chat_id: int, 
                                        context: ContextTypes.DEFAULT_TYPE) -> Optional[str]:
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
                result = await self._execute_tool(tool_name, params, chat_id, context)
                return self._format_tool_result_for_display(result)
        
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
                    # Vérifier si l'outil est sensible
                    tool_name = tool_call['tool']
                    params = tool_call['parameters']
                    
                    # Demander confirmation pour les outils sensibles
                    if self._is_sensitive_tool(tool_name):
                        # La confirmation sera gérée par le système d'autorisation
                        pass
                    
                    result = await self._execute_tool(tool_name, params, chat_id, context)
                    return self._format_tool_result_for_display(result)
            except json.JSONDecodeError:
                continue
        
        # JSON tool call simple (sans code fence)
        if message_stripped.startswith('{') and message_stripped.endswith('}'):
            try:
                tool_call = json.loads(message_stripped)
                if 'tool' in tool_call and 'parameters' in tool_call:
                    result = await self._execute_tool(
                        tool_call['tool'],
                        tool_call['parameters'],
                        chat_id,
                        context
                    )
                    return self._format_tool_result_for_display(result)
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

    def _is_sensitive_tool(self, tool_name: str) -> bool:
        """Verifie si l'outil est sensible - toujours False car les autorisations sont désactivées."""
        return False
    
    def _extract_tool_name(self, message: str) -> Optional[str]:
        """Extrait le nom de l'outil depuis un message."""
        # Vérifier les prefixes d'outils directs
        tool_prefixes = ['bash:', 'read:', 'write:', 'grep:', 'list:', 'search:', 'fetch:']
        for prefix in tool_prefixes:
            if message.lower().startswith(prefix):
                return prefix[:-1]  # retourne 'bash', 'read', etc.
        
        # Vérifier le format JSON
        import re
        json_pattern = r'\{\s*"tool"\s*:\s*"([^"]+)"'
        match = re.search(json_pattern, message)
        if match:
            return match.group(1)
        
        # Vérifier les blocs JSON dans les code fences
        json_block_pattern = r'```(?:)?\s*\{\s*"tool"\s*:\s*"([^"]+)"'
        match = re.search(json_block_pattern, message, re.DOTALL)
        if match:
            return match.group(1)
        
        return None
    
    def _extract_tool_params(self, message: str, tool_name: str) -> Dict[str, Any]:
        """Extrait les paramètres d'un outil depuis un message."""
        params = {}
        
        # Pour les prefixes directs
        if message.lower().startswith(f"{tool_name}:"):
            content = message[len(tool_name)+1:].strip()
            if tool_name == 'bash':
                params['command'] = content
            elif tool_name == 'read':
                params['path'] = content
            elif tool_name == 'write':
                parts = content.split('|', 1)
                if len(parts) >= 2:
                    params['file_path'] = parts[0].strip()
                    params['content'] = parts[1].strip()
            elif tool_name == 'grep':
                params['pattern'] = content
            elif tool_name == 'list':
                params['path'] = content or None
            elif tool_name == 'search':
                params['query'] = content
            elif tool_name == 'fetch':
                parts = content.split('|', 1)
                params['url'] = parts[0].strip()
                params['format'] = parts[1].strip() if len(parts) > 1 else 'markdown'
            return params
        
        # Pour le format 
        import re
        json_pattern = r'\{\s*"tool"\s*:\s*"[^"]+"\s*,\s*"parameters"\s*:\s*({[^}]+)\s*\}'
        match = re.search(json_pattern, message, re.DOTALL)
        if match:
            try:
                param_str = match.group(1)
                # Remplacer les guillemets simples par des guillemets doubles pour le parsing
                param_str = re.sub(r"'([^']+)'", r'"\1"', param_str)
                params = json.loads(param_str)
                return params
            except json.JSONDecodeError:
                pass
        
        # Pour les blocs  dans les code fences
        json_block_pattern = r'```(?:json)?\s*\{\s*"tool"\s*:\s*"[^"]+"\s*,\s*"parameters"\s*:\s*({[^}]+)\s*\}\s*```'
        match = re.search(json_block_pattern, message, re.DOTALL)
        if match:
            try:
                param_str = match.group(1)
                param_str = re.sub(r"'([^']+)'", r'"\1"', param_str)
                params = json.loads(param_str)
                return params
            except json.JSONDecodeError:
                pass
        
        return params

    async def _ask_for_confirmation(self, update: Update, context: ContextTypes.DEFAULT_TYPE, tool_name: str, params: Dict[str, Any]):
        """Demande confirmation pour un outil avec boutons y/n/a (comme dans CLI)."""
        chat_id = update.effective_chat.id
        
        # Stocker les informations pour le callback
        self.pending_actions[chat_id] = {
            "tool_name": tool_name,
            "params": params,
            "action_type": tool_name,
            "action_content": params,
            "timestamp": time.time()
        }
        
        # Boutons comme dans le CLI : y, n, a
        keyboard = [
            [
                InlineKeyboardButton("Oui (y)", callback_data="tool_yes"),
                InlineKeyboardButton("Non (n)", callback_data="tool_no"),
                InlineKeyboardButton("Toujours (a)", callback_data="tool_always")
            ]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        params_str = "\n".join([f"  {k}: {v}" for k, v in params.items()]) if params else "(aucun paramètre)"
        
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"[WARN] *Confirmation requise*\n\n"
                 f"Outil: `{tool_name}`\n"
                 f"Paramètres:\n```\n{params_str}\n```\n\n"
                 f"Autoriser cette action ?\n"
                 f"  • Oui (y) - Une seule fois\n"
                 f"  • Non (n) - Refuser\n"
                 f"  • Toujours (a) - Autoriser pour la session",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )

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

    async def _collect_telegram_images(self, update: Update, context) -> List[Dict[str, Any]]:
        """Photos envoyées au bot → data: URL pour le canal multimodal NemApi."""
        from src.core.image_attachments import MAX_IMAGE_BYTES, MAX_IMAGES
        import base64

        incoming = getattr(update, "message", None)
        if incoming is None:
            return []
        candidates = []
        photo = getattr(incoming, "photo", None)
        if photo:
            candidates.append((photo[-1], "image/jpeg", "photo.jpg"))
        document = getattr(incoming, "document", None)
        if document and str(getattr(document, "mime_type", "") or "").startswith("image/"):
            candidates.append((document, document.mime_type, document.file_name or "image"))

        specs: List[Dict[str, Any]] = []
        for file_obj, mime, name in candidates[:MAX_IMAGES]:
            try:
                size = int(getattr(file_obj, "file_size", 0) or 0)
                if size > MAX_IMAGE_BYTES:
                    continue
                payload = await context.bot.get_file(file_obj.file_id).download_as_bytearray()
                raw = bytes(payload)
                if len(raw) > MAX_IMAGE_BYTES:
                    continue
                url = f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
                specs.append({"url": url, "name": name[:120]})
            except Exception as exc:  # noqa: BLE001 - une photo illisible ne bloque pas le message
                logger.warning(f"Image Telegram ignorée: {exc}")
        return specs

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
                    risk = self.executor.risk_for(act_type, act_content) if hasattr(self.executor, "risk_for") else "medium"
                    authorized = self._session_authorized_tools.get(chat_id, set())
                    if risk == "high" and act_type not in authorized:
                        await self._ask_for_confirmation(update, context, act_type, act_content)
                        await self._cleanup_thinking_message(context, chat_id, status_msg)
                        return
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
            return build_system_prompt(self.registry) or ""
        except Exception as e:
            logger.error(f"Erreur construction prompt système: {e}")
            return "You are NEMESIS. Use only the tools provided by the runtime."

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
            self.pending_config.pop(chat_id, None)
        
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
        
        elif data == "confirm_yes":
            if chat_id in self.pending_config:
                pending = self.pending_config[chat_id]
                message = pending.get("message", "")
                result = await self._handle_message_with_tools(message, chat_id, context)
                if result:
                    await context.bot.send_message(chat_id=chat_id, text=result)
                await query.edit_message_text("[OK] Operation confirmee.")
                self.pending_config.pop(chat_id, None)
            else:
                await query.edit_message_text("[ERROR] Aucune operation en attente.")
        
        elif data == "confirm_no":
            self.pending_config.pop(chat_id, None)
            await query.edit_message_text("[ERROR] Operation annulee.")
        
        elif data == "tool_yes":
            # Autoriser l'outil une seule fois
            if chat_id in self.pending_actions:
                pending = self.pending_actions[chat_id]
                tool_name = pending.get("tool_name", "")
                params = pending.get("params", {})
                
                # Exécuter l'outil
                result = await self._execute_tool(tool_name, params, chat_id, context)
                
                # Envoyer le résultat
                if result and result.get('success'):
                    await query.edit_message_text(f"[OK] Outil '{tool_name}' exécuté avec succès")
                    # Envoyer le contenu si disponible
                    content = result.get('content', result.get('output', ''))
                    if content:
                        await context.bot.send_message(chat_id=chat_id, text=f"Résultat:\n```\n{content[:1000]}\n```", parse_mode="Markdown")
                else:
                    error = result.get('error', 'Erreur inconnue') if result else 'Erreur' 
                    await query.edit_message_text(f"[ERROR] Échec de l'outil '{tool_name}': {error}")
                
                self.pending_actions.pop(chat_id, None)
            else:
                await query.edit_message_text("[ERROR] Aucune action en attente.")
        
        elif data == "tool_no":
            # Refuser l'outil
            if chat_id in self.pending_actions:
                self.pending_actions.pop(chat_id, None)
                await query.edit_message_text("[ERROR] Exécution de l'outil annulée.")
            else:
                await query.edit_message_text("[ERROR] Aucune action en attente.")
        
        elif data == "tool_always":
            # Autoriser toujours pour la session
            if chat_id in self.pending_actions:
                pending = self.pending_actions[chat_id]
                tool_name = pending.get("tool_name", "")
                params = pending.get("params", {})
                
                # Ajouter l'outil aux autorisations de session
                # Pour l'instant on exécute et on note que c'est autorisé pour la session
                result = await self._execute_tool(tool_name, params, chat_id, context)
                
                # Marquer l'outil comme autorisé pour la session
                self._session_authorized_tools.setdefault(chat_id, set()).add(tool_name)
                
                if result and result.get('success'):
                    await query.edit_message_text(f"[OK] Outil '{tool_name}' autorisé pour la session et exécuté avec succès")
                    content = result.get('content', result.get('output', ''))
                    if content:
                        await context.bot.send_message(chat_id=chat_id, text=f"Résultat:\n```\n{content[:1000]}\n```", parse_mode="Markdown")
                else:
                    error = result.get('error', 'Erreur inconnue') if result else 'Erreur'
                    await query.edit_message_text(f"[ERROR] Échec de l'outil '{tool_name}': {error}")
                
                self.pending_actions.pop(chat_id, None)
            else:
                await query.edit_message_text("[ERROR] Aucune action en attente.")
        
        elif data.startswith("confirm_tool_"):
            # Gestion de la confirmation d'outil
            chat_id_from_data = int(data.replace("confirm_tool_", ""))
            if chat_id_from_data in self.pending_actions:
                pending = self.pending_actions[chat_id_from_data]
                
                # Exécuter l'outil
                act_type = pending['action_type']
                act_content = pending['action_content']
                tool_result = await self._execute_tool(act_type, act_content, chat_id_from_data, context)
                
                # Construire le feedback avec un marqueur de corrélation
                from src.core.agent_tools import build_feedback_message, format_tool_feedback

                seq = self._feedback_sequences.get(chat_id_from_data, 0) + 1
                self._feedback_sequences[chat_id_from_data] = seq
                if isinstance(tool_result, dict):
                    if tool_result.get('success'):
                        feedback = build_feedback_message(
                            [format_tool_feedback(act_type, tool_result)], seq
                        )
                    else:
                        feedback = build_feedback_message(
                            [format_tool_feedback(
                                act_type,
                                {"success": False, "error": tool_result.get('error', 'Erreur inconnue')},
                            )],
                            seq,
                        )
                else:
                    feedback = build_feedback_message(
                        [f"Tool: {act_type}\nSuccess: true\nOutput:\n{str(tool_result)}"], seq
                    )
                
                # Envoyer le feedback à l'IA et continuer la conversation
                provider = self._get_provider(chat_id_from_data)
                if provider:
                    result = provider.send_message(feedback, role="tool_result")
                    if result and result.get('success'):
                        raw_response = result.get('response', '')
                        parsed = self.parser.parse(raw_response)
                        display_text = parsed.get('text', '')
                        
                        if display_text and not display_text.startswith("FEEDBACK:"):
                            # Envoyer la réponse
                            config = self._get_chat_config(chat_id_from_data)
                            provider_type = "nemapi"
                            model = config.get("nemapi", {}).get("model", "")
                            formatted_response = self._format_ai_response([display_text], provider_type, model)
                            await context.bot.send_message(chat_id=chat_id_from_data, text=formatted_response)
                
                await query.edit_message_text("[OK] Outil exécuté et feedback envoyé à l'IA")
                self.pending_actions.pop(chat_id_from_data, None)
            else:
                await query.edit_message_text("[ERROR] Aucune action en attente.")
        
        elif data.startswith("cancel_tool_"):
            # Annulation de l'outil
            chat_id_from_data = int(data.replace("cancel_tool_", ""))
            if chat_id_from_data in self.pending_actions:
                self.pending_actions.pop(chat_id_from_data, None)
                await query.edit_message_text("[ERROR] Exécution de l'outil annulée.")
            else:
                await query.edit_message_text("[ERROR] Aucune action en attente.")
        
        elif data == "history_clear":
            provider = self._get_provider(chat_id)
            if provider:
                provider.reset_conversation()
            await query.edit_message_text("[CLEAR] Conversation effacee.")


    # =============================================================================
    # DEMARRAGE
    # =============================================================================

    async def _post_init(self, application: Application) -> None:
        self._event_loop = asyncio.get_running_loop()
        self.bot = application.bot

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
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
        self.app.add_handler(MessageHandler(
            (filters.PHOTO | filters.Document.IMAGE) & ~filters.COMMAND, self.handle_message
        ))
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
