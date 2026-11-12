"""File-driven BGM queue. No MCP call or GPT process is used after enqueue."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
from pydantic import BaseModel, Field, ConfigDict, model_validator

from .config import API_URL, ARTIFACT_DIR, DATA_DIR, DB_PATH
from .gateway_catalog import Catalog
from .review_data import atomic_json, load_json, measure_audio, safe_file
from .review_evaluate import swap_mb

REQUEST_DIR = DATA_DIR / "bgm-requests"
STATE_DIR = DATA_DIR / "bgm-request-state"
REVIEW_DIR = DATA_DIR / "reviews" / "bgm-local-v1"


class BgmRequest(BaseModel):
    @model_validator(mode="before")
    @classmethod
    def validate_hq_raw(cls, value):
        from .hq_contract import MODEL, validate_call
        if isinstance(value, dict) and value.get("model") == MODEL:
            validate_call(value.get("prompt"), value.get("duration_seconds"), value.get("seed"),
                          MODEL, value.get("generation_settings", {}), "music")
        return value

    model_config = ConfigDict(extra="forbid")
    schema_version: int = Field(default=1, ge=1, le=1)
    request_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    title: str = Field(min_length=1, max_length=300)
    use_case: str = Field(min_length=1, max_length=300)
    brief: dict = Field(min_length=1)
    prompt: str = Field(min_length=1, max_length=2000)
    prompt_version: str = Field(default="1", max_length=80)
    model: str = Field(default="acestep-v15-turbo")
    duration_seconds: float = Field(ge=.25, le=30)
    seed: int = Field(ge=0, le=2**31 - 1)
    generation_settings: dict = Field(default_factory=dict)
    created_at: str


def create_request(args: argparse.Namespace) -> Path:
    request_id = uuid.uuid4().hex
    brief = {"intent": args.intent, "scene": args.scene, "mood": args.mood, "use": args.use_case}
    if getattr(args, "tag", None):
        brief["tags"] = Catalog.clean_tags(args.tag)
    item = BgmRequest(
        request_id=request_id, title=args.title, use_case=args.use_case,
        brief=brief, prompt=args.prompt, prompt_version=args.prompt_version,
        model=args.model, duration_seconds=args.duration, seed=args.seed,
        generation_settings={"bpm": args.bpm} if args.bpm is not None else {},
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    root = Path(args.request_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    target = root / f"{request_id}.json"
    temporary = root / f".{request_id}.tmp"
    with temporary.open("x", encoding="utf-8") as handle:
        json.dump(item.model_dump(), handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.flush(); os.fsync(handle.fileno())
    temporary.replace(target)
    return target


class GenerationClient:
    def __init__(self, url: str = API_URL): self.url = url
    def _call(self, method: str, path: str, **kwargs):
        with httpx.Client(base_url=self.url, timeout=15) as client:
            response = client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()
    def ready(self): return self._call("GET", "/ready")
    def submit(self, item: BgmRequest):
        payload = dict(prompt=item.prompt, duration_seconds=item.duration_seconds,
                       seed=item.seed, model=item.model, kind="music",
                       generation_settings=item.generation_settings,
                       title=item.title, use_case=item.use_case, brief=item.brief,
                       prompt_version=item.prompt_version)
        return self._call("POST", "/v1/jobs", json=payload,
                          headers={"Idempotency-Key": "bgm-file-" + item.request_id})
    def status(self, job_id: str): return self._call("GET", "/v1/jobs/" + job_id)


def evaluate_artifact(artifact_id: str) -> dict:
    command = [sys.executable, "-m", "audioforge.review_evaluate", "--ids", artifact_id]
    try:
        run = subprocess.run(command, capture_output=True, text=True, timeout=900, check=False)
        if run.returncode and not (REVIEW_DIR / artifact_id / "result.json").exists():
            raise RuntimeError((run.stderr or run.stdout or "review process failed")[-1200:])
        return load_json(REVIEW_DIR / artifact_id / "result.json")
    except Exception as exc:
        audio = safe_file(ARTIFACT_DIR, artifact_id, "audio.wav")
        result = {"artifact_id": artifact_id, "status": "incomplete", "total_score": None,
                  "score": None, "audio_processed": False, "dsp": measure_audio(audio),
                  "error": f"{type(exc).__name__}: {exc}",
                  "limitations": ["Model analysis did not complete; human listening is required."]}
        atomic_json(REVIEW_DIR / artifact_id / "result.json", result)
        return result


class FileWorkflow:
    def __init__(self, request_dir=REQUEST_DIR, state_dir=STATE_DIR, *, generation=None,
                 catalog=None, reviewer=None, artifact_dir=ARTIFACT_DIR, allow_mock=False,
                 memory_guard=None):
        self.request_dir = Path(request_dir)
        self.state_dir = Path(state_dir)
        self.generation = generation or GenerationClient()
        self.artifact_dir = Path(artifact_dir)
        self.catalog = catalog or Catalog(DB_PATH, self.artifact_dir)
        self.reviewer = reviewer or evaluate_artifact
        self.memory_guard = memory_guard or swap_mb
        self.allow_mock = allow_mock
        self.request_dir.mkdir(parents=True, exist_ok=True)
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def run_once(self) -> list[dict]:
        results = []
        def priority(path: Path) -> tuple[int, str]:
            state_path = self.state_dir / path.name
            try:
                deferred = state_path.is_file() and load_json(state_path).get("phase") == "analysis_deferred"
            except (OSError, ValueError, TypeError, AttributeError):
                deferred = False
            return (int(deferred), path.name)

        for path in sorted(self.request_dir.glob("*.json"), key=priority):
            if path.is_symlink() or not path.is_file(): continue
            try:
                item = BgmRequest.model_validate(load_json(path))
                if path.stem != item.request_id: raise ValueError("request ID and filename differ")
                state_path = self.state_dir / path.name
                state = load_json(state_path) if state_path.exists() else {"request_id": item.request_id, "phase": "new"}
                if state["phase"] in ("done", "generation_failed", "invalid", "submission_blocked"): continue
                advanced = self.advance(item, state)
                deferred = advanced is None
                if not deferred:
                    state = advanced
                state["updated_at"] = datetime.now(timezone.utc).isoformat()
                atomic_json(state_path, state)
                results.append(state)
                # A deferred new request consumes no work slot; preserve the existing one-work-item quota.
                if not deferred:
                    break
            except Exception as exc:
                if path.stem and len(path.stem) == 32 and all(c in '0123456789abcdef' for c in path.stem):
                    state_path = self.state_dir / path.name
                    old = load_json(state_path) if state_path.exists() else {"request_id": path.stem, "phase": "new"}
                    old.update(last_error=f"{type(exc).__name__}: {exc}", updated_at=datetime.now(timezone.utc).isoformat())
                    if old["phase"] == "new" and isinstance(exc, (ValueError, json.JSONDecodeError)):
                        old["phase"] = "invalid"
                    atomic_json(state_path, old)
                    results.append(old)
        return results

    def advance(self, item: BgmRequest, state: dict) -> dict | None:
        phase = state["phase"]
        if phase == "new":
            ready = self.generation.ready()
            if ready.get("backend") == "mock" and not self.allow_mock:
                raise RuntimeError("generation backend is mock; real BGM queue is paused")
            from .hq_contract import MODEL as HQ_MODEL
            hq = ready.get("profiles", {}).get(HQ_MODEL, {}) if item.model == HQ_MODEL else {}
            if item.model == HQ_MODEL and hq.get("enabled") is not True:
                state.update(last_error="HQ profile unavailable; submission deferred")
                return None
            if ready.get("ready") is False:
                state.update(last_error="generation service is not ready; submission deferred")
                return None
            try:
                job = self.generation.submit(item)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in (409, 422):
                    raise
                state.update(phase="submission_blocked",
                             submission_error_status=exc.response.status_code,
                             last_error=f"generation submission blocked (HTTP {exc.response.status_code}); explicit resolution required")
                return state
            state.update(phase="submitted", job_id=job["id"], last_error=None)
            return state
        if phase == "submitted":
            job = self.generation.status(state["job_id"])
            if job["status"] in ("queued", "running"): return state
            if job["status"] != "completed":
                state.update(phase="generation_failed", last_error=job.get("error") or job["status"])
                return state
            state.update(phase="generated", artifact_id=job["artifact_id"])
            phase = "generated"
        if phase == "generated":
            aid = state["artifact_id"]
            metadata = load_json(safe_file(self.artifact_dir, aid, "metadata.json"))
            if metadata.get("synthetic_test_artifact") is not False and not self.allow_mock:
                raise RuntimeError("synthetic artifact cannot enter real BGM review")
            song, _ = self.catalog.register({
                "idempotency_key": "bgm-file-" + item.request_id,
                "generation_job_id": state["job_id"], "artifact_id": aid,
                "title": item.title, "use_case": item.use_case, "brief": item.brief,
                "prompt": item.prompt, "prompt_version": item.prompt_version,
                "seed": item.seed, "model": metadata.get("model", item.model),
                "generation_settings": item.generation_settings,
                "source_metadata": metadata, "review_status": "unreviewed",
            })
            state.update(phase="registered", song_id=song["song_id"])
            # Persist the irreversible registration before model analysis.
            atomic_json(self.state_dir / f"{item.request_id}.json", state)
            phase = "registered"
        if phase in ("registered", "analysis_deferred"):
            used_swap = self.memory_guard()
            if used_swap >= 1536:
                state.update(phase="analysis_deferred", last_error=f"model analysis deferred: swap {used_swap:.0f} MiB >= 1536 MiB")
                return state
            review = self.reviewer(state["artifact_id"])
            reviewed = review.get("status") == "evaluated" and review.get("total_score") is not None
            self.catalog.update(state["song_id"], {
                "review_id": "bgm-local-v1/" + state["artifact_id"],
                "rubric_version": review.get("rubric_version"),
                "review_status": "completed" if reviewed else "partial",
                "review": review, "score": review.get("score") if reviewed else None,
                "total_score": review.get("total_score") if reviewed else None,
            })
            deferred = "preflight swap" in str(review.get("error", "")) or "memory safety" in str(review.get("error", ""))
            state.update(phase="analysis_deferred" if deferred else "done",
                         review_status="completed" if reviewed else "partial",
                         last_error=review.get("error") if deferred else None)
        return state


def main() -> None:
    parser = argparse.ArgumentParser(description="AudioForge file-driven BGM workflow")
    subs = parser.add_subparsers(dest="command", required=True)
    create = subs.add_parser("create", help="write one immutable request JSON")
    for name in ("title", "use-case", "intent", "scene", "mood", "prompt"):
        create.add_argument("--" + name, required=True)
    create.add_argument("--duration", type=float, default=30)
    create.add_argument("--seed", type=int, default=42)
    create.add_argument("--bpm", type=int)
    create.add_argument("--model", default="acestep-v15-turbo")
    create.add_argument("--prompt-version", default="1")
    create.add_argument("--tag", action="append", help="library hashtag; repeat for multiple tags")
    create.add_argument("--request-dir", type=Path, default=REQUEST_DIR)
    worker = subs.add_parser("worker", help="poll requests periodically")
    worker.add_argument("--interval", type=float, default=30)
    worker.add_argument("--once", action="store_true")
    worker.add_argument("--request-dir", type=Path, default=REQUEST_DIR)
    worker.add_argument("--state-dir", type=Path, default=STATE_DIR)
    args = parser.parse_args()
    if args.command == "create":
        print(create_request(args))
        return
    if args.interval < 1: parser.error("interval must be >= 1 second")
    lock_path = Path(args.state_dir).resolve().parent / "bgm-file-worker.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = lock_path.open("a")
    try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError: parser.error("another BGM file worker is running")
    workflow = FileWorkflow(args.request_dir, args.state_dir)
    if args.once:
        for result in workflow.run_once(): print(json.dumps(result, ensure_ascii=False), flush=True)
        return
    stop = False
    def requested_stop(_signal, _frame):
        nonlocal stop
        stop = True
    signal.signal(signal.SIGTERM, requested_stop)
    signal.signal(signal.SIGINT, requested_stop)
    while not stop:
        for result in workflow.run_once(): print(json.dumps(result, ensure_ascii=False), flush=True)
        for _ in range(int(args.interval * 10)):
            if stop: break
            time.sleep(.1)


if __name__ == "__main__": main()
