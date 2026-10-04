#!/usr/bin/env python3
"""NEMESIS coding-agent CLI entry point.

This interface reuses exactly the same engine, tools and slash commands as
`agent.py`. It only differs in presentation intent. It exists as a parallel,
evaluable entry point.

Usage:
    python cli.py [--debug] [--mock]
"""

import sys
from typing import List, Optional

from agent import NemesisApp


class CodingAgentApp(NemesisApp):
    """Coding-agent oriented presentation of the same NEMESIS engine."""

    def run(self):
        # Same behaviour as the historical UI for now. Kept as a subclass so
        # the presentation can evolve independently without touching agent.py.
        return super().run()


def main(argv: Optional[List[str]] = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    debug_mode = "--debug" in args
    mock_mode = "--mock" in args

    app = CodingAgentApp(debug=debug_mode)
    if mock_mode:
        from providers.mock import MockProvider

        import agent as agent_module

        def _mock_provider(config):
            return MockProvider(config)

        agent_module.create_provider = _mock_provider

    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())