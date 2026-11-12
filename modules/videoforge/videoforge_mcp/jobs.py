from __future__ import annotations
import json
import os
import tempfile
from pathlib import Path
from typing import Any
from .config import JOB_DIR

def job_path(job_id: str) -> Path:
    if not job_id.replace("-", "").isalnum():
        raise ValueError("invalid job id")
    return JOB_DIR / f"{job_id}.json"

def write_job(job_id: str, data: dict[str, Any]) -> None:
    JOB_DIR.mkdir(parents=True, exist_ok=True)
    target = job_path(job_id)
    fd, name = tempfile.mkstemp(prefix=f".{job_id}.", suffix=".tmp", dir=JOB_DIR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        Path(name).replace(target)
    finally:
        Path(name).unlink(missing_ok=True)

def read_job(job_id: str) -> dict[str, Any]:
    path = job_path(job_id)
    if not path.is_file():
        raise FileNotFoundError(job_id)
    return json.loads(path.read_text(encoding="utf-8"))
