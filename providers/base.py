#!/usr/bin/env python3
"""Classe de base pour tous les providers LLM."""

import json
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional


class BaseProvider(ABC):
    """Interface commune pour tous les providers LLM."""

    supports_images = False

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.provider_config = config.get("provider", {})
        self.host = None
        self.port = None
        self.model = self.provider_config.get("model", "")
        self._conversation: List[Dict[str, str]] = []

    @abstractmethod
    def send_message(
        self,
        message: str,
        role: str = "user",
        images: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Envoie un message au LLM.

        ``images`` (optionnel) contient des parts OpenAI ``image_url`` ; seuls
        les providers avec ``supports_images = True`` les acceptent.
        Retourne {'success': True, 'response': str} ou {'success': False, 'error': str}.
        """
        ...

    @abstractmethod
    def test_connection(self) -> bool:
        """Teste si le provider est joignable."""
        ...

    def list_models(self) -> List[Dict[str, Any]]:
        """Liste les modeles disponibles. Pour le bridge, retourne vide."""
        return []

    def reset_conversation(self):
        """Efface l'historique de conversation."""
        self._conversation = []

    def get_conversation(self) -> List[Dict[str, str]]:
        """Retourne la conversation en cours."""
        return list(self._conversation)

    def set_conversation(self, messages: List[Dict[str, str]]):
        """Restaure une conversation sauvegardee."""
        self._conversation = list(messages)

    def conversation_count(self) -> int:
        """Nombre de messages dans la conversation."""
        return len(self._conversation)

    def save_conversation(self, path: str) -> bool:
        """Sauvegarde la conversation dans un fichier JSON."""
        try:
            with open(path, "w") as f:
                json.dump(self._conversation, f, indent=2, ensure_ascii=False)
            return True
        except Exception:
            return False

    def load_conversation(self, path: str) -> bool:
        """Charge une conversation depuis un fichier JSON."""
        try:
            with open(path, "r") as f:
                self._conversation = json.load(f)
            return True
        except Exception:
            return False

    def upload_file(self, path: str) -> Dict[str, Any]:
        """Upload un fichier (optionnel, non supporte par tous les providers)."""
        return {"success": False, "error": "Upload non supporte par ce provider"}

    def ask_with_file(self, path: str, question: str) -> Dict[str, Any]:
        """Pose une question avec un fichier (optionnel)."""
        return {"success": False, "error": "ask_with_file non supporte par ce provider"}
