"""Modern NEMESIS terminal application entry point.

The CLI shares its agent loop with ``agent.py`` so both the legacy command and
the ``nemesis-cli`` console script use the same tools, memory and settings.
"""

import sys
from pathlib import Path
from typing import List, Optional

from rich.console import Console

from agent import NemesisApp, main as agent_main
from src.ui.chat_ui import ChatUI
from src.ui.theme import NEMESIS_THEME

__all__ = ["NemesisApp", "main", "run_demo"]


def run_demo(console: Optional[Console] = None) -> None:
    """Show a sample conversation and tool results without contacting providers."""
    console = console or Console(theme=NEMESIS_THEME)
    chat = ChatUI(console)
    chat.welcome(
        provider="DÉMO",
        target="hors ligne · aucun appel réseau",
        model="exemple interactif",
        workspace=str(Path.cwd()),
    )
    chat.add_message(
        "Lis le guide du projet et lance les tests.",
        "Vous",
    )
    chat.add_message(
        "Je parcours le guide puis lance les tests. Voici le résumé.",
        "NEMESIS",
    )
    chat.tool_start("read_file", {"path": "README.md", "limit": 8})
    chat.tool_result(
        {
            "success": True,
            "stdout": "Texte de démonstration masqué pour les outils hors terminal.",
        },
        tool_name="read_file",
        duration=0.02,
    )
    chat.tool_start("bash", {"command": "pytest -q", "description": "Vérifier la suite de tests"})
    chat.tool_result(
        {"success": True, "stdout": "3 passed, 0 failed  (résultat fictif)"},
        tool_name="bash",
        duration=0.84,
    )
    chat.add_message(
        "Le guide est lu et les tests passent. Les appels restent compacts; "
        "seule la commande terminal affiche sa sortie. Cet aperçu est entièrement fictif.",
        "NEMESIS",
    )
    console.print("[dim]Démo locale : actions et sortie fictives, aucun provider ni outil exécuté.[/dim]")


def main(argv: Optional[List[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--demo" in args:
        run_demo()
        return 0
    return agent_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
