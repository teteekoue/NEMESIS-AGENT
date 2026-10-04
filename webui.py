"""Web UI launcher (alternative to `python3 agent.py`).

Usage:
  python3 webui.py                   # start web server on http://0.0.0.0:8080
  python3 webui.py --port 9000       # custom port
  python3 webui.py --mock            # offline mock provider
  python3 webui.py --workspace DIR   # override workspace
"""

import argparse
import sys
from pathlib import Path

# Ensure repo root on path so `web`, `providers`, etc. are importable.
sys.path.insert(0, str(Path(__file__).resolve().parent))


def main() -> int:
    from web.server import main as web_main

    return web_main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
