from __future__ import annotations
import json
import os
import threading
import time
import uuid
from pathlib import Path
from .backends import Cancelled, make_backend
from .config import ARTIFACT_DIR, BACKEND, MODEL_LICENSE, MODEL_VERSION
from .store import Store

class Worker:
    def __init__(self, store: Store, artifacts: Path = ARTIFACT_DIR, backend_name: str = BACKEND):
        self.store=store; self.artifacts=Path(artifacts); self.backend_name=backend_name; self.stop_event=threading.Event(); self.thread=None
    def start(self):
        self.store.recover_running()
        if not self.thread or not self.thread.is_alive(): self.thread=threading.Thread(target=self.run,daemon=True,name="audioforge-worker"); self.thread.start()
    def stop(self): self.stop_event.set(); self.thread and self.thread.join(timeout=3)
    def run(self):
        while not self.stop_event.is_set():
            job=self.store.next_job()
            if not job: self.stop_event.wait(.1); continue
            request=job["request"]; artifact_id=uuid.uuid4().hex; output=self.artifacts/artifact_id/"audio.wav"
            try:
                from .hq_contract import MODEL as HQ_MODEL, validate_payload
                if request.get("model") == HQ_MODEL:
                    validate_payload(request)
                    from .hq_backend import HQBackend
                    root = os.environ.get("AUDIOFORGE_ACESTEP_ROOT")
                    if not root:
                        raise RuntimeError("HQ_RUNTIME_ROOT_REQUIRED")
                    backend = HQBackend(runtime_root=Path(root), artifact_root=self.artifacts)
                else:
                    backend=make_backend(self.backend_name)
                provenance=backend.generate(request["prompt"],request["duration_seconds"],request["seed"],request["model"],output,lambda:self.store.get(job["id"])["cancel_requested"],request.get("generation_settings",{}))
                if self.store.get(job["id"])["cancel_requested"]: raise Cancelled()
                is_mock=bool(provenance.get("synthetic_test_artifact")); metadata={"artifact_id":artifact_id,"job_id":job["id"],"title":request.get("title"),"use_case":request.get("use_case"),"brief":request.get("brief") or {},"prompt_version":request.get("prompt_version","1"),"prompt":request["prompt"],"duration_seconds":provenance.get("duration_seconds",request["duration_seconds"]),"seed":request["seed"],"requested_model":request["model"],"model":provenance.get("model",request["model"]),"model_version":provenance.get("model_version",MODEL_VERSION),"license":provenance.get("license",MODEL_LICENSE),"backend":backend.name,"sample_rate":provenance.get("sample_rate",44100),"channels":provenance.get("channels",2),"kind":request["kind"],"synthetic_test_artifact":is_mock,"provenance":provenance}
                (output.parent/"metadata.json").write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding="utf-8")
                self.store.finish(job["id"],artifact_id)
            except Cancelled: self.store.cancel(job["id"])
            except Exception as exc: self.store.fail_or_retry(job["id"],f"{type(exc).__name__}: {exc}")
