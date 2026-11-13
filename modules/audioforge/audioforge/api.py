from __future__ import annotations
import json
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from .config import ARTIFACT_DIR, BACKEND, DB_PATH, MODEL, MODEL_LICENSE, MODEL_VERSION
from .schemas import GenerateRequest, LoopPostprocessRequest
from .service import Worker
from .store import Store, IdempotencyConflict
from .hq_contract import MODEL as HQ_MODEL, PROFILE_VERSION, enabled

def create_app(db_path: Path = DB_PATH, artifact_dir: Path = ARTIFACT_DIR, backend: str = BACKEND) -> FastAPI:
    store=Store(db_path); worker=Worker(store,artifact_dir,backend)
    @asynccontextmanager
    async def lifespan(app): worker.start(); yield; worker.stop()
    app=FastAPI(title="AudioForge Generation API",version="0.1.0",lifespan=lifespan)
    app.state.store=store; app.state.worker=worker; app.state.artifact_dir=Path(artifact_dir)

    @app.get("/health")
    def health(): return {"ok":True,"service":"audioforge","queue":"sqlite","concurrency":1}

    def hq_profile():
        return {"model": HQ_MODEL, "profile_version": PROFILE_VERSION,
                "enabled": enabled(), "status": "enabled_unverified" if enabled() else "disabled",
                "runtime_verified": False, "model_loaded": False}

    @app.get("/ready")
    def ready(): return {"ready":True,"backend":backend,"model_loaded":False,"lazy_load":True,"profile":MODEL,
                         "profiles": {HQ_MODEL: hq_profile()}}

    @app.post("/v1/jobs",status_code=202)
    async def submit(request: GenerateRequest, raw_request: Request, idempotency_key: str|None=Header(default=None,alias="Idempotency-Key")):
        payload=request.model_dump()
        if request.model == HQ_MODEL:
            # Preserve exact nested types and explicit values for durable replay/conflict checks.
            payload.update(await raw_request.json())
        if request.model == HQ_MODEL:
            body_key=payload.pop("idempotency_key",None)
            key=idempotency_key or body_key
        else:
            key=idempotency_key or payload.pop("idempotency_key",None)
        try:
            job,created=store.submit(payload,key)
        except IdempotencyConflict as exc:
            raise HTTPException(409,str(exc))
        except ValueError as exc:
            raise HTTPException(422,str(exc))
        except RuntimeError as exc:
            if str(exc) == "HQ_PROFILE_NOT_ACTIVATED":
                raise HTTPException(503,str(exc))
            raise
        return JSONResponse({"created":created,**job},status_code=202 if created else 200)

    @app.get("/v1/jobs/{job_id}")
    def get_job(job_id:str):
        try:return store.get(job_id)
        except KeyError:raise HTTPException(404,"job not found")

    @app.delete("/v1/jobs/{job_id}")
    def cancel_job(job_id:str):
        try:return store.request_cancel(job_id)
        except KeyError:raise HTTPException(404,"job not found")

    @app.get("/v1/artifacts/{artifact_id}")
    def artifact(artifact_id:str):
        path=Path(artifact_dir)/artifact_id/"audio.wav"
        if not path.is_file():raise HTTPException(404,"artifact not found")
        return FileResponse(path,media_type="audio/wav",filename=f"{artifact_id}.wav")

    @app.get("/v1/artifacts/{artifact_id}/metadata")
    def artifact_metadata(artifact_id:str):
        path=Path(artifact_dir)/artifact_id/"metadata.json"
        if not path.is_file():raise HTTPException(404,"artifact not found")
        return json.loads(path.read_text(encoding="utf-8"))

    @app.post("/v1/postprocess/loop")
    def loop_contract(request:LoopPostprocessRequest):
        raise HTTPException(501,"loop post-processing is a separate contract and is not enabled in the SFX MVP")

    @app.get("/v1/profiles")
    def profiles():
        return {"active":{"provider":"stability-ai","profile":"small-sfx","backend":backend,"model_version":MODEL_VERSION,"license":MODEL_LICENSE},"expandable":[{"profile":"small-music","enabled":False},{"profile":"medium-mlx","enabled":False},{"provider":"ace-step","profile":"2b","enabled":False}],"license_acceptance_performed":False,"hq":hq_profile()}
    return app

app=create_app()

def main():
    import uvicorn
    uvicorn.run("audioforge.api:app",host="127.0.0.1",port=8765,workers=1)
