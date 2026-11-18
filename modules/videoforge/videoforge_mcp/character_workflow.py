"""File requests and catalog for one-time portraits and repeated animations."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from .config import JOB_DIR, OUTPUT_DIR
from .contracts import atomic_json, sha256
from .queue import JobQueue

ROOT = Path(os.environ.get("VIDEOFORGE_CHARACTER_ROOT", Path(__file__).resolve().parents[1] / "data" / "characters"))
PROFILE = Path(os.environ.get("VIDEOFORGE_PROFILE_PATH", Path(__file__).resolve().parents[1] / "profiles" / "ltx-video-2b-0.9.8-distilled-bf16-i2v.json"))
MODEL_ROOT = Path(os.environ.get("VIDEOFORGE_I2V_MODEL_DIR", Path(__file__).resolve().parents[1] / "models" / "ltx-video-i2v"))
KEY = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def check_key(value: str) -> str:
    if not isinstance(value, str) or not KEY.fullmatch(value):
        raise ValueError("invalid_key")
    return value


def path_for(kind: str, key: str) -> Path:
    return ROOT / kind / (check_key(key) + ".json")


def load(kind: str, key: str) -> dict:
    return json.loads(path_for(kind, key).read_text(encoding="utf-8"))


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 12000:
        raise ValueError(f"invalid_{name}")
    return value.strip()


def validate_portrait(data: dict) -> dict:
    if set(data) != {"schema_version", "portrait_key", "title", "planning", "prompt", "source_png", "rights_attested", "created_at"} or data["schema_version"] != 1:
        raise ValueError("invalid_portrait_schema")
    if data["rights_attested"] is not True:
        raise ValueError("rights_attestation_required")
    check_key(data["portrait_key"])
    for field in ("title", "planning", "prompt", "created_at"):
        _text(data[field], field)
    source = Path(_text(data["source_png"], "source_png")).resolve(strict=True)
    intake = (ROOT / "intake").resolve(strict=True)
    if not source.is_relative_to(intake) or source.suffix.lower() != ".png" or not source.is_file():
        raise ValueError("portrait_source_must_be_intake_png")
    with Image.open(source) as im:
        if im.format != "PNG" or im.mode not in ("RGBA", "LA") or im.width * im.height > 16_000_000:
            raise ValueError("transparent_png_required")
        alpha = im.getchannel("A")
        if alpha.getextrema()[0] >= 255:
            raise ValueError("actual_transparency_required")
    return data


def validate_animation(data: dict) -> dict:
    fields = {"schema_version", "animation_key", "portrait_key", "title", "planning", "prompt", "negative_prompt", "model_options", "created_at"}
    if set(data) != fields or data["schema_version"] != 1:
        raise ValueError("invalid_animation_schema")
    check_key(data["animation_key"]); check_key(data["portrait_key"])
    for field in ("title", "planning", "prompt", "created_at"):
        _text(data[field], field)
    if not isinstance(data["negative_prompt"], str) or len(data["negative_prompt"]) > 12000:
        raise ValueError("invalid_negative_prompt")
    options = data["model_options"]
    if not isinstance(options, dict) or set(options) != {"profile_id", "width", "height", "frames", "fps", "steps", "seed", "timeout_seconds"}:
        raise ValueError("invalid_model_options")
    profile = json.loads(PROFILE.read_text(encoding="utf-8"))
    if options["profile_id"] != profile["profile_id"]:
        raise ValueError("unsupported_profile")
    limits = profile["limits"]
    for field, lo, hi in (("width", 64, limits["width"]), ("height", 64, limits["height"]), ("frames", 2, limits["frames"]), ("fps", 1, limits["fps"]), ("steps", 1, 40), ("seed", 0, 2147483647), ("timeout_seconds", 1, limits["timeout_seconds"])):
        value = options[field]
        if type(value) is not int or not lo <= value <= hi:
            raise ValueError(f"invalid_{field}")
    if options["width"] % 8 or options["height"] % 8:
        raise ValueError("dimensions_must_be_multiples_of_8")
    return data


def create(kind: str, data: dict) -> Path:
    key = data["portrait_key"] if kind == "portrait-requests" else data["animation_key"]
    (validate_portrait if kind == "portrait-requests" else validate_animation)(data)
    target = path_for(kind, key)
    if target.exists():
        raise FileExistsError(target)
    atomic_json(target, data)
    return target


def process_portraits() -> None:
    for request in sorted((ROOT / "portrait-requests").glob("*.json")):
        key = request.stem
        target = path_for("portraits", key)
        if target.exists():
            continue
        try:
            data = validate_portrait(json.loads(request.read_text(encoding="utf-8")))
            if data["portrait_key"] != key:
                raise ValueError("filename_key_mismatch")
            source = Path(data["source_png"])
            asset = ROOT / "portrait-assets" / (key + ".png")
            asset.parent.mkdir(parents=True, exist_ok=True)
            if asset.exists():
                raise ValueError("portrait_asset_exists")
            temp = asset.with_name("." + key + "." + uuid.uuid4().hex + ".png")
            try:
                shutil.copyfile(source, temp)
                temp.replace(asset)
            finally:
                temp.unlink(missing_ok=True)
            atomic_json(target, {**data, "status": "ready", "asset_sha256": sha256(asset), "registered_at": now()})
        except Exception as exc:
            atomic_json(path_for("errors", "portrait-" + key), {"error": str(exc)[:300], "updated_at": now()})


def process_animations(queue: JobQueue | None = None) -> None:
    queue = queue or JobQueue(JOB_DIR, ROOT)
    for request in sorted((ROOT / "animation-requests").glob("*.json")):
        key = request.stem
        target = path_for("animations", key)
        try:
            data = validate_animation(json.loads(request.read_text(encoding="utf-8")))
            if data["animation_key"] != key:
                raise ValueError("filename_key_mismatch")
            portrait = load("portraits", data["portrait_key"])
            image = ROOT / "portrait-assets" / (data["portrait_key"] + ".png")
            if portrait.get("status") != "ready" or sha256(image) != portrait["asset_sha256"]:
                raise ValueError("portrait_not_ready")
            record = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {**data, "status": "pending", "updated_at": now()}
            if record.get("job_id"):
                job = queue.get(record["job_id"])
                record["status"] = job["status"]
                record["updated_at"] = now()
                if job["status"] == "completed":
                    record["artifact"] = job["artifact"]
                elif job["status"] in ("failed", "cancelled"):
                    record["error"] = job.get("error", {"code": job["status"]})
                atomic_json(target, record)
                continue
            profile = json.loads(PROFILE.read_text(encoding="utf-8"))
            model_file = MODEL_ROOT / profile["model"]["artifact"]
            if not model_file.is_file():
                record.update(status="waiting_for_model", updated_at=now())
                atomic_json(target, record)
                continue
            opts = data["model_options"]
            job = queue.submit({"idempotency_key": key, "profile_id": opts["profile_id"],
                                "input_image": {"path": str(image), "consent": True, "rights_attested": portrait["rights_attested"]},
                                "prompt": data["prompt"], "negative_prompt": data["negative_prompt"],
                                "model_dir": str(MODEL_ROOT), **{k: opts[k] for k in ("width", "height", "frames", "fps", "steps", "seed", "timeout_seconds")}})
            record.update(status=job["status"], job_id=job["job_id"], updated_at=now())
            atomic_json(target, record)
        except FileNotFoundError:
            atomic_json(path_for("errors", "animation-" + key), {"error": "portrait_key_not_found", "updated_at": now()})
        except Exception as exc:
            atomic_json(path_for("errors", "animation-" + key), {"error": str(exc)[:300], "updated_at": now()})


def run_once() -> None:
    (ROOT / "intake").mkdir(parents=True, exist_ok=True)
    process_portraits()
    process_animations()


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("portrait")
    for name in ("portrait-key", "title", "planning", "prompt", "source-png"):
        p.add_argument("--" + name, required=True)
    p.add_argument("--rights-attested", action="store_true", required=True)
    a = sub.add_parser("animation")
    for name in ("animation-key", "portrait-key", "title", "planning", "prompt"):
        a.add_argument("--" + name, required=True)
    a.add_argument("--negative-prompt", default="")
    for name, default in (("width", 320), ("height", 576), ("frames", 33), ("fps", 8), ("steps", 8), ("seed", 42), ("timeout-seconds", 1800)):
        a.add_argument("--" + name, type=int, default=default)
    sub.add_parser("once")
    loop = sub.add_parser("loop"); loop.add_argument("--interval", type=float, default=30)
    args = parser.parse_args()
    if args.command == "portrait":
        data = {"schema_version": 1, "portrait_key": args.portrait_key, "title": args.title,
                "planning": args.planning, "prompt": args.prompt, "source_png": str(Path(args.source_png).resolve()),
                "rights_attested": args.rights_attested, "created_at": now()}
        print(create("portrait-requests", data))
    elif args.command == "animation":
        profile = json.loads(PROFILE.read_text(encoding="utf-8"))
        opts = {"profile_id": profile["profile_id"], **{name: getattr(args, name) for name in ("width", "height", "frames", "fps", "steps", "seed", "timeout_seconds")}}
        data = {"schema_version": 1, "animation_key": args.animation_key, "portrait_key": args.portrait_key,
                "title": args.title, "planning": args.planning, "prompt": args.prompt,
                "negative_prompt": args.negative_prompt, "model_options": opts, "created_at": now()}
        print(create("animation-requests", data))
    elif args.command == "once":
        run_once()
    else:
        while True:
            run_once(); time.sleep(args.interval)


if __name__ == "__main__":
    main()
