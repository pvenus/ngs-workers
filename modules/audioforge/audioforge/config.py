from __future__ import annotations
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.getenv("AUDIOFORGE_DATA_DIR", ROOT / "data")).expanduser().resolve()
DB_PATH = Path(os.getenv("AUDIOFORGE_DB_PATH", DATA_DIR / "queue.sqlite3")).expanduser().resolve()
ARTIFACT_DIR = Path(os.getenv("AUDIOFORGE_ARTIFACT_DIR", DATA_DIR / "artifacts")).expanduser().resolve()
API_URL = os.getenv("AUDIOFORGE_API_URL", "http://127.0.0.1:8765").rstrip("/")
BACKEND = os.getenv("AUDIOFORGE_BACKEND", "mock")
MODEL = os.getenv("AUDIOFORGE_MODEL", "small-sfx")
MODEL_VERSION = os.getenv("AUDIOFORGE_MODEL_VERSION", "stable-audio-3")
MODEL_LICENSE = os.getenv("AUDIOFORGE_MODEL_LICENSE", "Stable Audio Community License")
STABLE_AUDIO_MLX_DIR = Path(os.getenv("AUDIOFORGE_STABLE_AUDIO_MLX_DIR", ROOT / "vendor" / "stable-audio-3" / "optimized" / "mlx")).expanduser().resolve()
MAX_ATTEMPTS = max(1, int(os.getenv("AUDIOFORGE_MAX_ATTEMPTS", "2")))
