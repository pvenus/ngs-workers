from __future__ import annotations
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODEL_ID = os.getenv("VIDEOFORGE_MODEL_ID", "ali-vilab/text-to-video-ms-1.7b")
MODEL_DIR = Path(os.getenv("VIDEOFORGE_MODEL_DIR", PROJECT_ROOT / "models" / "text-to-video-ms-1.7b-fp16")).expanduser().resolve()
OUTPUT_DIR = Path(os.getenv("VIDEOFORGE_OUTPUT_DIR", PROJECT_ROOT / "outputs")).expanduser().resolve()
JOB_DIR = Path(os.getenv("VIDEOFORGE_JOB_DIR", PROJECT_ROOT / "jobs")).expanduser().resolve()
