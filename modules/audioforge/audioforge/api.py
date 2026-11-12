from __future__ import annotations
import json
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from .config import ARTIFACT_DIR, BACKEND, DB_PATH, MODEL, MODEL_LICENSE, MODEL_VERSION
from .schemas import GenerateRequest, LoopPostprocessRequest
from .service import Worker
from .store import Store

def create_app(db_path: Path = DB_PATH, artifact_dir: Path = ARTIFACT_DIR, backend: str = BACKEND) -> FastAPI:
    store=Store(db_path); worker=Worker(store,artifact_dir,backend)
    @asynccontextmanager
    async def lifespan(app): worker.start(); yield; worker.stop()
    app=FastAPI(title="AudioForge Generation API",version="0.1.0",lifespan=lifespan)
    app.state.store=store; app.state.worker=worker; app.state.artifact_dir=Path(artifact_dir)

    @app.get("/health")
    def health(): return {"ok":True,"service":"audioforge","queue":"sqlite","concurrency":1}

    @app.get("/ready")
    def ready(): return {"ready":True,"backend":backend,"model_loaded":False,"lazy_load":True,"profile":MODEL}

    @app.post("/v1/jobs",status_code=202)
    def submit(request: GenerateRequest,idempotency_key: str|None=Header(default=None,alias="Idempotency-Key")):
        payload=request.model_dump(); key=idempotency_key or payload.pop("idempotency_key",None)
        job,created=store.submit(payload,key); return JSONResponse({"created":created,**job},status_code=202 if created else 200)

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
        return {"active":{"provider":"stability-ai","profile":"small-sfx","backend":backend,"model_version":MODEL_VERSION,"license":MODEL_LICENSE},"expandable":[{"profile":"small-music","enabled":False},{"profile":"medium-mlx","enabled":False},{"provider":"ace-step","profile":"2b","enabled":False}],"license_acceptance_performed":False}
    return app

app=create_app()

def main():
    import uvicorn
    uvicorn.run("audioforge.api:app",host="127.0.0.1",port=8765,workers=1)
