#!/usr/bin/env python3
"""Deterministic offline provider used by `cli.py --mock`.

It lets the real agent loop, parser and tool handlers be exercised without a
NEMAPI server. Given a natural-language prompt it emits canonical JSON tool
calls, then summarizes the FEEDBACK it receives.
"""

import json
from typing import Any, Dict, List

from providers.base import BaseProvider


def _bash(command: str) -> str:
    return json.dumps({"tool": "bash", "parameters": {"command": command}})


class MockProvider(BaseProvider):
    """Scripted, offline replacement for a real LLM provider."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.model = self.model or "mock"
        self._turn = 0

    def test_connection(self) -> bool:
        return True

    def list_models(self) -> List[Dict[str, Any]]:
        return [{"id": "mock", "name": "mock"}]

    def _first_action(self, message: str) -> str:
        text = message.lower()
        if "lis" in text or "read" in text or "fichier" in text:
            return json.dumps({"tool": "read_file", "parameters": {"path": "README.md"}})
        if "bash" in text or "commande" in text:
            return _bash("echo 'hello from mock'")
        if "modif" in text or "write" in text or "écri" in text:
            return json.dumps({
                "tool": "write_file",
                "parameters": {"file_path": "mock_demo.txt", "content": "demo\n"},
            })
        if "erreur" in text or "error" in text:
            return _bash("sh -c 'echo boom >&2; exit 1'")
        if "plan" in text or "todo" in text:
            return json.dumps({
                "tool": "todo",
                "parameters": {
                    "action": "set",
                    "items": [
                        {"content": "Inspecter", "status": "completed"},
                        {"content": "Modifier", "status": "in_progress"},
                        {"content": "Valider", "status": "pending"},
                    ],
                },
            })
        if "tous les outils" in text or "teste" in text:
            return json.dumps([
                _bash_json("echo one"),
                _bash_json("echo two"),
            ])
        return "Mock: aucune action reconnue. Essayez `lis un fichier`."

    def send_message(self, message: str, role: str = "user") -> Dict[str, Any]:
        self._turn += 1
        if isinstance(message, str) and message.startswith("FEEDBACK:"):
            return {"success": True, "response": "Termine. Resultat:\n" + message[:1500]}
        if role == "system":
            return {"success": True, "response": "OK"}
        return {"success": True, "response": self._first_action(message or "")}


def _bash_json(command: str) -> Dict[str, Any]:
    return {"tool": "bash", "parameters": {"command": command}}