"""Run the NEMESIS web API: ``python -m web.backend``."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import uvicorn  # noqa: E402


def main() -> None:
    host = os.environ.get("NEMESIS_WEB_HOST", "0.0.0.0")
    port = int(os.environ.get("NEMESIS_WEB_PORT", "8000"))
    uvicorn.run("web.backend.app:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
