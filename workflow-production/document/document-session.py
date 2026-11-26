"""Isolated structured authoring; all output shape validation also runs in the worker."""
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from codex_session import serve


def run(session, call):
    if (set(call)-{"images"} != {"binary", "cwd", "prompt", "schema", "timeoutMs", "outputLimit"} or
            any(not os.path.isabs(call[k]) for k in ("binary", "cwd")) or
            not 0 < call["timeoutMs"] <= 300000 or not 0 < call["outputLimit"] <= 12 * 1024 * 1024):
        raise ValueError("input")
    images=call.get("images",[])
    if type(images) is not list or len(images)>12 or any(type(n) is not str or not re.fullmatch(r"map-image-[0-9]{2}\.png",n) for n in images):
        raise ValueError("input")
    root=Path(call["cwd"])
    paths=[root/n for n in images]
    if any(p.is_symlink() or not p.is_file() for p in paths):raise ValueError("input")
    result, _, _ = session.run(call["binary"], Path(call["cwd"]), call["prompt"], call["schema"],
                               call["timeoutMs"]/1000, call["outputLimit"], paths)
    return result


if __name__ == "__main__":
    serve("document", run)
