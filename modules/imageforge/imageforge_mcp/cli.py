from __future__ import annotations

import os
import sys
from pathlib import Path

import uvicorn


def main() -> None:
    """Run the local ImageForge HTTP API and browser UI."""
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from .http_server import app

    host = os.environ.get("IMAGEFORGE_HOST", "127.0.0.1")
    port = int(os.environ.get("IMAGEFORGE_PORT", "8000"))
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
