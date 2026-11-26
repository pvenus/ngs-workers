"""Isolated structured authoring; all output shape validation also runs in the worker."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from codex_session import serve


def run(session, call):
    if (set(call) != {"binary", "cwd", "prompt", "schema", "timeoutMs", "outputLimit"} or
            any(not os.path.isabs(call[k]) for k in ("binary", "cwd")) or
            not 0 < call["timeoutMs"] <= 300000 or not 0 < call["outputLimit"] <= 12 * 1024 * 1024):
        raise ValueError("input")
    result, _, _ = session.run(call["binary"], Path(call["cwd"]), call["prompt"], call["schema"],
                               call["timeoutMs"]/1000, call["outputLimit"])
    return result


if __name__ == "__main__":
    serve("document", run)
