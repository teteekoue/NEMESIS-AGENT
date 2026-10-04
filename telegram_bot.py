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
AWAITING_NEMAPI_HOST = 5

# Repertoire de stockage des configurations du bot
BOT_CONFIG_DIR = Path(__file__).parent / "telegram_bot_configs"


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
        "security": {"workspace": str(Path("./workspace").resolve())},
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

    def __init__(self, token: str, workspace: str = "./workspace",
                 default_config: Optional[Dict[str, Any]] = None):
        """Initialise le bot Telegram NEMESIS."""
        self.token = token
        self.workspace = workspace
        defaults = default_config or {}
        default_nemapi = defaults.get("nemapi", {}) if isinstance(defaults, dict) else {}
        self.default_nemapi = {
            "host": str(default_nemapi.get("host") or "127.0.0.1"),
            "port": int(default_nemapi.get("port") or 8080),
            "model": str(default_nemapi.get("model") or ""),
        }
        # Access control: empty list = everyone allowed (backward compatible).
        # Values can come from config (top-level or security) or env var.
        raw_allowed = (
            defaults.get("allowed_users")
            or (defaults.get("security") or {}).get("allowed_users")
            or os.getenv("TELEGRAM_ALLOWED_USERS", "")
        ) if isinstance(defaults, dict) else os.getenv("TELEGRAM_ALLOWED_USERS", "")
        if isinstance(raw_allowed, str):
            raw_allowed = [x.strip() for x in raw_allowed.split(",") if x.strip()]
        self.allowed_users = {str(x).strip() for x in (raw_allowed or []) if str(x).strip()}
        self.bot: Optional[Bot] = None
        self.app: Optional[Application] = None
        self.registry = None
        self.executor = ToolBridge(workspace=self.workspace)
        self.parser = ActionParser.from_registry(self.executor.registry)
        self.providers: Dict[int, Any] = {}
        self._session_authorized_tools: Dict[int, set] = {}
        # High-risk calls are authorized by exact signature (tool + params) so a
        # one-time approval cannot be reused for a different destructive command.
        self._session_authorized_signatures: Dict[int, set] = {}
        
        # Etats de conversation par chat
        self.chat_states: Dict[int, int] = {}
        
        # Donnees temporaires
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
            "read_file, write_file, bash, grep, list_dir, web_search, web_fetch\n"
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
            "       /fetch: url\n\n"
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

    def _is_user_allowed(self, update: Update) -> bool:
        """Check the allow-list. Empty list means everyone is allowed."""
        if not self.allowed_users:
            return True
        user = getattr(update, "effective_user", None)
        candidates = set()
        if user is not None:
            if getattr(user, "id", None) is not None:
                candidates.add(str(user.id))
            if getattr(user, "username", None):
                candidates.add(str(user.username))
        chat = getattr(update, "effective_chat", None)
        if chat is not None and getattr(chat, "id", None) is not None:
            candidates.add(str(chat.id))
        return bool(candidates & self.allowed_users)

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Gere les messages texte."""
        if not self._is_user_allowed(update):
            logger.warning("Message refusé d'un utilisateur non autorisé")
            return
        chat_id = update.effective_chat.id
        message_text = update.message.text
        
        if not message_text:
            return
        
        config = self._get_chat_config(chat_id)
        
        # Gestion des etats de configuration
        if chat_id in self.chat_states:
            state = self.chat_states[chat_id]
            
            if state == AWAITING_NEMAPI_HOST:
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
            handled, tool_result = await self._handle_message_with_tools(message_text, chat_id, context)
            if handled:
                if tool_result:
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
        handled, tool_result = await self._handle_message_with_tools(message_text, chat_id, context)
        if handled:
            if tool_result:
                await context.bot.send_message(chat_id=chat_id, text=tool_result)
            return
        
        # Traiter comme une requete pour l'IA
        await self._process_ai_request(update, context, message_text, chat_id)


    # =============================================================================
    # OUTILS
    # =============================================================================

    async def _handle_message_with_tools(self, message: str, chat_id: int,
                                        context: ContextTypes.DEFAULT_TYPE, update=None):
        """Traite un message qui peut contenir des appels d'outils.

        Returns (handled, result_text). handled=True means the message was a
        direct tool call (already executed or awaiting confirmation)."""
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
                return await self._authorize_and_execute(
                    update, context, tool_name, params, chat_id
                )

        # Détection des appels d'outils au format JSON avec code fence
        import re
        json_block_pattern = r'```(?:)?\s*(\{[^`]*\})\s*```'
        matches = re.findall(json_block_pattern, message_stripped, re.DOTALL)

        for json_str in matches:
            try:
                tool_call = json.loads(json_str.strip())
                if 'tool' in tool_call and 'parameters' in tool_call:
                    return await self._authorize_and_execute(
                        update, context, tool_call['tool'], tool_call['parameters'], chat_id
                    )
            except json.JSONDecodeError:
                continue

        # JSON tool call simple (sans code fence)
        if message_stripped.startswith('{') and message_stripped.endswith('}'):
            try:
                tool_call = json.loads(message_stripped)
                if 'tool' in tool_call and 'parameters' in tool_call:
                    return await self._authorize_and_execute(
                        update, context, tool_call['tool'], tool_call['parameters'], chat_id
                    )
            except json.JSONDecodeError:
                pass

        return False, None

    @staticmethod
    def _auth_signature(tool_name: str, params: Dict[str, Any]) -> str:
        import hashlib
        try:
            blob = json.dumps(params, sort_keys=True, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            blob = repr(params)
        return f"{tool_name}:{hashlib.sha256(blob.encode('utf-8', 'replace')).hexdigest()}"

    async def _authorize_and_execute(self, update, context, tool_name: str,
                                     params: Dict[str, Any], chat_id: int):
        """Authorize a direct (prefixed/JSON) tool call before executing it.

        Read-only tools run immediately. Every other tool requires explicit
        confirmation, mirroring the CLI behaviour. Returns (handled, result)."""
        if not tool_name:
            return False, None
        risk = self.executor.risk_for(tool_name, params)
        if risk != "read":
            authorized = self._session_authorized_tools.get(chat_id, set())
            signatures = self._session_authorized_signatures.get(chat_id, set())
            signature = self._auth_signature(tool_name, params)
            if tool_name not in authorized and signature not in signatures:
                if update is not None:
                    await self._ask_for_confirmation(update, context, tool_name, params)
                    return True, None
                return True, "[ERROR] Autorisation requise; aucune confirmation possible."
        result = await self._execute_tool(tool_name, params, chat_id, context)
        return True, self._format_tool_result_for_display(result)
    
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

    async def _ask_for_confirmation(self, update: Update, context: ContextTypes.DEFAULT_TYPE, tool_name: str, params: Dict[str, Any]):
        """Demande confirmation pour un outil avec boutons y/n/a (comme dans CLI)."""
        effective = getattr(update, "effective_chat", None)
        if effective is not None:
            chat_id = effective.id
        elif getattr(update, "message", None) is not None:
            chat_id = update.message.chat_id
        else:
            chat_id = update.callback_query.message.chat_id
        
        # Stocker les informations pour le callback en préservant le contexte
        # de reprise éventuellement déjà posé par la boucle agent.
        pending = self.pending_actions.setdefault(chat_id, {})
        pending.update({
            "tool_name": tool_name,
            "params": params,
            "action_type": tool_name,
            "action_content": params,
            "timestamp": time.time(),
        })

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
        risk = self.executor.risk_for(tool_name, params) if hasattr(self.executor, "risk_for") else "medium"

        await context.bot.send_message(
            chat_id=chat_id,
            text=f"[WARN] Confirmation requise (risque: {risk})\n\n"
                 f"Outil: {tool_name}\n"
                 f"Paramètres:\n{params_str}\n\n"
                 f"Autoriser cette action ?\n"
                 f"  • Oui (y) - Une seule fois\n"
                 f"  • Non (n) - Refuser\n"
                 f"  • Toujours (a) - Autoriser pour la session",
            reply_markup=reply_markup,
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
                    'append': 'append_file',
                    'replace': 'edit',
                }
                tool_name = alt_names.get(tool_name, tool_name)

            if tool_name not in self.registry._tools:
                available_tools = list(self.registry._tools.keys())
                return {"success": False, "error": f"Outil inconnu: {tool_name}"}

            parameters = self._normalize_parameters(tool_name, parameters)
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
            
            return {
                "success": success,
                "content": content,
                "error": error,
                "output": content  # Pour compatibilité
            }
            
        except Exception as e:
            logger.error(f"Erreur outil {tool_name}: {e}")
            return {"success": False, "error": str(e), "content": "", "output": ""}

    async def _process_ai_request(self, update: Update, context: ContextTypes.DEFAULT_TYPE, 
                                  message: str, chat_id: int):
        """Traite une requete pour l'IA avec autorisation des outils."""
        provider = self._get_provider(chat_id)
        if not provider:
            await context.bot.send_message(chat_id=chat_id, text="[ERROR] Provider non disponible")
            return

        # Envoyer le prompt système si nécessaire
        if not self.prompt_system_sent.get(chat_id, False):
            await self._send_system_prompt(chat_id)

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
            await self._run_agent_loop(update, context, chat_id, message, "user", status_msg)
        except Exception as e:
            await self._cleanup_thinking_message(context, chat_id, status_msg)
            logger.error(f"Erreur dans _process_ai_request: {e}")
            await context.bot.send_message(chat_id=chat_id, text=f"[ERROR] Erreur: {str(e)[:1000]}")

    async def _run_agent_loop(self, update, context, chat_id: int, current_input: str,
                              current_role: str, status_msg, last_ai_text: str = ""):
        """Agent turn loop. Pauses and asks confirmation via buttons when a tool
        requires authorization; the button handler resumes the loop with FEEDBACK."""
        provider = self._get_provider(chat_id)
        if not provider:
            return
        config = self._get_chat_config(chat_id)
        model = config.get("nemapi", {}).get("model", "")

        iteration = 0
        max_iterations = 10
        while iteration < max_iterations:
            iteration += 1
            try:
                result = provider.send_message(current_input, role=current_role)
            except TypeError:
                result = provider.send_message(current_input)

            if not result or not isinstance(result, dict):
                await self._cleanup_thinking_message(context, chat_id, status_msg)
                await context.bot.send_message(chat_id=chat_id, text="[ERROR] Réponse invalide")
                return

            if not result.get("success"):
                await self._cleanup_thinking_message(context, chat_id, status_msg)
                error = result.get('error', 'Erreur inconnue')
                await context.bot.send_message(chat_id=chat_id, text=f"[ERROR] {error}")
                return

            raw_response = result.get('response', '')
            parsed = self.parser.parse(raw_response)
            display_text = parsed.get('text', '')
            action = parsed.get('action')

            if display_text and not display_text.startswith("FEEDBACK:"):
                import re
                clean_text = re.sub(r'```\s*\{[^`]*\}\s*```', '', display_text, flags=re.DOTALL)
                clean_text = re.sub(r'\{\s*"tool"\s*:.*?\}', '', clean_text, flags=re.DOTALL)
                clean_text = clean_text.strip()
                if clean_text:
                    last_ai_text = clean_text

            actions = parsed.get("actions") or ([action] if action else [])
            if not actions:
                break

            feedback_parts = []
            for action_item in actions:
                act_type = action_item.get('type', '')
                act_content = action_item.get('content', {})
                risk = self.executor.risk_for(act_type, act_content) if hasattr(self.executor, "risk_for") else "medium"
                authorized = self._session_authorized_tools.get(chat_id, set())
                signatures = self._session_authorized_signatures.get(chat_id, set())
                signature = self._auth_signature(act_type, act_content)
                if risk != "read" and act_type not in authorized and signature not in signatures:
                    # Store context so the button handler can resume the loop.
                    self.pending_actions[chat_id] = {
                        "tool_name": act_type,
                        "params": act_content,
                        "action_type": act_type,
                        "action_content": act_content,
                        "last_ai_text": last_ai_text,
                        "status_msg": status_msg,
                        "timestamp": time.time(),
                        "resume": True,
                    }
                    await self._ask_for_confirmation(update, context, act_type, act_content)
                    return
                tool_result = await self._execute_tool(act_type, act_content, chat_id, context)
                if isinstance(tool_result, dict):
                    if tool_result.get('success'):
                        feedback_parts.append(
                            f"Tool: {act_type}\nSuccess: true\nOutput:\n"
                            f"{tool_result.get('content', tool_result.get('output', 'OK'))}"
                        )
                    else:
                        feedback_parts.append(
                            f"Tool: {act_type}\nSuccess: false\nError:\n"
                            f"{tool_result.get('error', 'Erreur inconnue')}"
                        )
                else:
                    feedback_parts.append(f"Tool: {act_type}\nSuccess: true\nOutput:\n{tool_result}")

            current_input = "FEEDBACK:\n" + "\n\n".join(feedback_parts)
            current_role = "tool_result"

        await self._cleanup_thinking_message(context, chat_id, status_msg)
        if last_ai_text:
            formatted_response = self._format_ai_response([last_ai_text], "nemapi", model)
            await self._send_long_message(context, chat_id, formatted_response)
        else:
            await context.bot.send_message(chat_id=chat_id, text="[INFO] Aucune réponse textuelle générée.")

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

    async def _send_system_prompt(self, chat_id: int) -> bool:
        """Envoye le prompt système à l'IA (pas à l'utilisateur!) pour ce chat."""
        # Generate the prompt from the live registry so Telegram and CLI
        # expose exactly the same tools and rules.
        try:
            from src.core.agent_tools import build_system_prompt
            prompt = build_system_prompt(self.registry)
        except Exception as e:
            logger.error(f"Erreur construction prompt système: {e}")
            prompt = "You are NEMESIS. Use only the tools provided by the runtime."
        
        try:
            provider = self._get_provider(chat_id)
            if not provider:
                logger.error("Provider non disponible pour envoyer le prompt système")
                return False
            
            # Envoyer le prompt à l'IA via le provider, pas à l'utilisateur!
            # Certains providers (nemapi_bridge) supportent role=, d'autres non
            try:
                result = provider.send_message(prompt, role="system")
            except TypeError:
                # Provider ne supporte pas role=, envoyer sans
                result = provider.send_message(prompt)
            
            if result and result.get("success"):
                self.prompt_system_sent[chat_id] = True
                return True
            else:
                logger.error(f"Echec envoi prompt système: {result}")
                return False
        except Exception as e:
            logger.error(f"Erreur envoi prompt système: {e}")
            return False

    def _format_ai_response(self, outputs: List[str], provider_type: str, model: str) -> str:
        """Formate la réponse de l'IA comme dans le CLI NEMESIS."""
        combined_text = '\n\n'.join(outputs)
        
        # Nettoyer les balises HTML restantes
        import re
        combined_text = re.sub(r'<[^>]+>', '', combined_text)
        
        # Formater sans Markdown pour éviter les erreurs de parsing
        if model:
            header = f"⚡ NEMESIS [{provider_type}] ({model}) :"
        else:
            header = f"⚡ NEMESIS [{provider_type}] :"
        
        return f"{header}\n\n{combined_text}"
    
    async def _send_long_message(self, context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str):
        """Envoie un message long en le découpant si nécessaire (limite 4096 caractères)."""
        MAX_LENGTH = 3900  # Légèrement en dessous de la limite pour éviter les problèmes de parse
        
        # Nettoyer le texte pour éviter les erreurs de parsing Markdown
        import re
        # Remplacer les caractères problématiques
        text = re.sub(r'([*_`])(?![^*_`]*\1)', r'\\\1', text)  # Échapper les caractères Markdown simples
        # Remplacer les triples backticks problématiques
        text = re.sub(r'```(?:[a-zA-Z]+)?\n', '```\n', text)
        
        if len(text) <= MAX_LENGTH:
            try:
                await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")
            except Exception:
                # En cas d'erreur Markdown, envoyer sans formatage
                await context.bot.send_message(chat_id=chat_id, text=text)
            return
        
        # Découper le message en plusieurs parties
        parts = []
        current_part = ""
        
        for line in text.split('\n'):
            if len(current_part) + len(line) + 1 > MAX_LENGTH:
                parts.append(current_part)
                current_part = line
            else:
                if current_part:
                    current_part += '\n' + line
                else:
                    current_part = line
        
        if current_part:
            parts.append(current_part)
        
        # Envoyer chaque partie avec un indicateur de progression
        for i, part in enumerate(parts, 1):
            if len(parts) > 1:
                header = f"[Partie {i}/{len(parts)}]\n\n"
                part = header + part
            try:
                await context.bot.send_message(chat_id=chat_id, text=part, parse_mode="Markdown")
            except Exception:
                # En cas d'erreur Markdown, envoyer sans formatage
                await context.bot.send_message(chat_id=chat_id, text=part)

    def _format_tool_result_for_display(self, tool_result: Dict[str, Any]) -> str:
        """Formate un résultat d'outil (dict) en chaîne pour affichage."""
        if isinstance(tool_result, dict):
            if not tool_result.get('success', True):
                error = tool_result.get('error', 'Erreur inconnue')
                return f"[ERROR] Erreur outil: {error}"
            content = tool_result.get('content', tool_result.get('output', 'OK'))
            if isinstance(content, str) and len(content) > 10000:  # 10 Ko - augmente de 3 Ko
                content = content[:10000] + "\n\n... (tronque)"
            return str(content)
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
            if result.get('success'):
                content = result.get('content', result.get('output', result.get('stdout', 'OK')))
                if isinstance(content, str) and len(content) > 10000:  # 10 Ko
                    content = content[:10000] + "\n\n... (tronque)"
                return content
            else:
                error = result.get('error', result.get('message', 'Erreur inconnue'))
                return f"[ERROR] Erreur: {error}"
        
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
        if not self._is_user_allowed(update):
            await query.answer("Acces refuse.", show_alert=True)
            return
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
        
        elif data in ("tool_yes", "tool_always"):
            # Autoriser l'outil (une fois) ou pour la session
            if chat_id in self.pending_actions:
                pending = self.pending_actions.pop(chat_id)
                tool_name = pending.get("tool_name", "")
                params = pending.get("params", {})
                resume = pending.get("resume", False)
                last_ai_text = pending.get("last_ai_text", "")
                status_msg = pending.get("status_msg")
                risk = self.executor.risk_for(tool_name, params) if hasattr(self.executor, "risk_for") else "medium"

                if data == "tool_always":
                    if risk == "high":
                        self._session_authorized_signatures.setdefault(chat_id, set()).add(
                            self._auth_signature(tool_name, params)
                        )
                    else:
                        self._session_authorized_tools.setdefault(chat_id, set()).add(tool_name)

                result = await self._execute_tool(tool_name, params, chat_id, context)

                if not resume:
                    # Direct (prefixed/JSON) call: just report the result.
                    if result and result.get('success'):
                        await query.edit_message_text(f"[OK] Outil '{tool_name}' exécuté avec succès")
                        content = result.get('content', result.get('output', ''))
                        if content:
                            await context.bot.send_message(chat_id=chat_id, text=f"Résultat:\n{content[:1000]}")
                    else:
                        error = result.get('error', 'Erreur inconnue') if result else 'Erreur'
                        await query.edit_message_text(f"[ERROR] Échec de l'outil '{tool_name}': {error}")
                    return

                # Agent-loop call: resume the loop with the tool feedback.
                await query.edit_message_text(f"[OK] Outil '{tool_name}' autorisé.")
                if isinstance(result, dict) and result.get('success'):
                    feedback = (f"Tool: {tool_name}\nSuccess: true\nOutput:\n"
                                f"{result.get('content', result.get('output', 'OK'))}")
                else:
                    error = result.get('error', 'Erreur inconnue') if isinstance(result, dict) else str(result)
                    feedback = f"Tool: {tool_name}\nSuccess: false\nError:\n{error}"
                await self._run_agent_loop(
                    update, context, chat_id, "FEEDBACK:\n" + feedback,
                    "tool_result", status_msg, last_ai_text,
                )
            else:
                await query.edit_message_text("[ERROR] Aucune action en attente.")

        elif data == "tool_no":
            # Refuser l'outil
            if chat_id in self.pending_actions:
                pending = self.pending_actions.pop(chat_id)
                tool_name = pending.get("tool_name", "")
                if pending.get("resume"):
                    await query.edit_message_text("[ERROR] Exécution annulée.")
                    feedback = f"Tool: {tool_name}\nSuccess: false\nOutput:\n[EXECUTION REFUSEE PAR L'UTILISATEUR]"
                    await self._run_agent_loop(
                        update, context, chat_id, "FEEDBACK:\n" + feedback,
                        "tool_result", pending.get("status_msg"), pending.get("last_ai_text", ""),
                    )
                    return
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

    def run(self):
        """Demarre le bot."""
        if not self.token:
            raise ValueError("Token Telegram manquant!")
        
        self.app = (Application.builder()
            .token(self.token)
            .connect_timeout(30.0)
            .read_timeout(30.0)
            .write_timeout(30.0)
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
    """Charge la configuration du bot depuis un fichier YAML."""
    import yaml
    config_path = Path(config_path)
    if not config_path.exists():
        return {}
    with open(config_path, 'r') as f:
        return yaml.safe_load(f) or {}


def main():
    """Point d'entree principal."""
    config = load_bot_config()
    token = os.getenv('TELEGRAM_BOT_TOKEN') or config.get('token')
    workspace = config.get('workspace', './workspace')
    
    if not token:
        print("[ERROR] Erreur: Token Telegram manquant!")
        print("   Definissez TELEGRAM_BOT_TOKEN ou creez telegram_config.yaml")
        sys.exit(1)
    
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
