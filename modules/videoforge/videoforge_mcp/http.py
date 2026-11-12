"""Loopback HTTP boundary for uploaded VideoForge videos and candidate frames."""
from __future__ import annotations

import fcntl
import json
import math
import os
import re
import shutil
import tempfile
import uuid
import zipfile
from pathlib import Path

import imageio.v3 as iio
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from .config import JOB_DIR, OUTPUT_DIR
from .contracts import selection_manifest, sha256
from .frames import extract_candidates
from .paths import confined
from .character_workflow import ROOT as CHARACTER_ROOT, PROFILE as CHARACTER_PROFILE, check_key, create as create_character_request, now as character_now

MAX_UPLOAD_BYTES = 512 * 1024 * 1024
MAX_MULTIPART_OVERHEAD_BYTES = 1024 * 1024
MAX_FRAMES = 12000
MAX_TARGET_N = 60
IDENT = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")
TOKEN = re.compile(r"^[a-f0-9]{32}$")


class ApiError(Exception):
    def __init__(self, status: int, code: str):
        self.status, self.code = status, code


def _id(value: str, token: bool = False) -> str:
    if not isinstance(value, str) or not (TOKEN if token else IDENT).fullmatch(value):
        raise ApiError(400, "invalid_identifier")
    return value


async def _json(request: Request) -> dict:
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError) as exc:
        raise ApiError(400, "invalid_request") from exc
    if not isinstance(body, dict):
        raise ApiError(400, "invalid_request")
    return body


def _root(request: Request) -> Path:
    root = Path(request.app.state.output_root).resolve(strict=True)
    return root


def _no_links(path: Path, root: Path) -> None:
    current = path
    while current != root:
        if current.is_symlink():
            raise ApiError(409, "path_conflict")
        current = current.parent


def _project(request: Request) -> Path:
    root = _root(request)
    project = root / "http" / "projects" / _id(request.path_params["project"])
    _no_links(project, root)
    return confined(project, _root(request))


def _source(request: Request, upload_id: str) -> Path:
    path = _project(request) / "uploads" / (_id(upload_id, True) + ".mp4")
    _no_links(path, _root(request))
    path = confined(path, _root(request))
    if not path.is_file():
        raise ApiError(404, "upload_not_found")
    return path


def _candidate_dir(request: Request) -> Path:
    path = _project(request) / "extractions" / _id(request.path_params["extraction_id"], True)
    _no_links(path, _root(request))
    return confined(path, _root(request))


def _manifest(request: Request) -> tuple[Path, dict]:
    directory = _candidate_dir(request)
    raw_path = directory / "candidates.json"
    _no_links(raw_path, _root(request))
    path = confined(raw_path, _root(request))
    if not path.is_file():
        raise ApiError(404, "extraction_not_found")
    return directory, json.loads(path.read_text(encoding="utf-8"))


def _public_manifest(request: Request, manifest: dict) -> dict:
    base = f"/api/projects/{request.path_params['project']}/extractions/{request.path_params['extraction_id']}"
    source = {key: value for key, value in manifest["source"].items() if key != "path"}
    return {"schema_version": 1, "extraction_id": request.path_params["extraction_id"],
            "source": source, "target_n": manifest["target_n"],
            "candidate_limit": manifest["candidate_limit"],
            "candidates": [{key: value for key, value in item.items() if key != "path"} |
                           {"preview_url": f"{base}/candidates/{item['candidate_id']}/preview",
                            "download_url": f"{base}/candidates/{item['candidate_id']}/download"}
                           for item in manifest["candidates"]],
            "selection_url": f"{base}/selection", "zip_url": f"{base}/download.zip",
            "diagnostics": manifest.get("diagnostics", [])}


def _frame_count(source: Path) -> int:
    try:
        raw = iio.immeta(source).get("nframes")
        if isinstance(raw, (int, float)) and math.isfinite(raw) and raw > 0:
            count = int(raw)
            if count > MAX_FRAMES:
                raise ApiError(413, "frame_limit_exceeded")
            return count
        count = 0
        for _ in iio.imiter(source):
            count += 1
            if count > MAX_FRAMES:
                raise ApiError(413, "frame_limit_exceeded")
        if count == 0:
            raise ApiError(422, "invalid_video")
        return count
    except ApiError:
        raise
    except Exception as exc:
        raise ApiError(422, "invalid_video") from exc


async def health(request: Request) -> Response:
    return JSONResponse({"status": "ok", "service": "videoforge-http"})


def _character_action(request: Request) -> None:
    if request.headers.get("x-forge-action") != "1":
        raise ApiError(403, "forge_action_required")


async def character_schemas(request: Request) -> Response:
    profile = json.loads(CHARACTER_PROFILE.read_text(encoding="utf-8"))
    key = {"type": "string", "pattern": "^[a-z][a-z0-9_-]{1,63}$"}
    string = {"type": "string", "minLength": 1, "maxLength": 12000}
    defaults = {"profile_id": profile["profile_id"], "width": 320, "height": 576,
                "frames": 33, "fps": 8, "steps": 8, "seed": 42, "timeout_seconds": 1800}
    option_properties = {name: {"type": "integer", "default": value} for name, value in defaults.items() if name != "profile_id"}
    option_properties["profile_id"] = {"type": "string", "const": profile["profile_id"]}
    return JSONResponse({"schema_version": 1,
        "portrait": {"content_type": "multipart/form-data", "type": "object", "additionalProperties": False,
                     "required": ["portrait_key", "title", "planning", "prompt", "png", "rights_attested"],
                     "properties": {"portrait_key": key, "title": string, "planning": string,
                                    "prompt": string, "png": {"type": "string", "format": "binary", "description": "Transparent RGBA/LA PNG, max 16 megapixels, request max 32 MiB"},
                                    "rights_attested": {"type": "boolean", "const": True}}},
        "animation": {"content_type": "application/json", "type": "object", "additionalProperties": False,
                      "required": ["animation_key", "portrait_key", "title", "planning", "prompt"],
                      "properties": {"animation_key": key, "portrait_key": key, "title": string,
                                     "planning": string, "prompt": string,
                                     "negative_prompt": {"type": "string", "maxLength": 12000, "default": ""},
                                     "model_options": {"type": "object", "properties": option_properties,
                                                       "defaults": defaults, "limits": profile["limits"]}}}})


async def submit_portrait(request: Request) -> Response:
    _character_action(request)
    limit = 32 * 1024 * 1024
    raw_length = request.headers.get("content-length", "")
    if not raw_length.isdecimal() or int(raw_length) > limit:
        raise ApiError(413, "portrait_upload_too_large")
    try:
        form = await request.form(max_files=1, max_fields=5)
        key = check_key(str(form.get("portrait_key", "")))
        file = form.get("png")
        if file is None or not hasattr(file, "read") or not str(file.filename or "").lower().endswith(".png"):
            raise ApiError(415, "png_required")
        if str(form.get("rights_attested", "")).lower() not in ("true", "on", "1"):
            raise ApiError(422, "rights_attestation_required")
        intake = CHARACTER_ROOT / "intake"
        intake.mkdir(parents=True, exist_ok=True)
        target = intake / (key + ".png")
        if target.exists() or any((CHARACTER_ROOT / kind / (key + ".json")).exists() for kind in ("portrait-requests", "portraits")):
            raise ApiError(409, "portrait_key_exists")
        created = False
        try:
            with target.open("xb") as output:
                created = True
                size = 0
                while block := await file.read(65536):
                    size += len(block)
                    if size > limit:
                        raise ApiError(413, "portrait_upload_too_large")
                    output.write(block)
            data = {"schema_version": 1, "portrait_key": key,
                    "title": str(form.get("title", "")), "planning": str(form.get("planning", "")),
                    "prompt": str(form.get("prompt", "")), "source_png": str(target.resolve()),
                    "rights_attested": True, "created_at": character_now()}
            create_character_request("portrait-requests", data)
        except Exception:
            if created:
                target.unlink(missing_ok=True)
            raise
        return JSONResponse({"portrait_key": key, "phase": "queued"}, status_code=202)
    except ValueError as exc:
        raise ApiError(422, str(exc)) from exc
    except FileExistsError as exc:
        raise ApiError(409, "portrait_key_exists") from exc
    except HTTPException as exc:
        raise ApiError(400, "invalid_multipart") from exc
    finally:
        if "form" in locals():
            await form.close()


async def submit_animation(request: Request) -> Response:
    _character_action(request)
    raw_length = request.headers.get("content-length", "")
    if not raw_length.isdecimal() or int(raw_length) > 65536:
        raise ApiError(413, "request_too_large")
    body = await _json(request)
    try:
        if not set(body).issubset({"animation_key", "portrait_key", "title", "planning", "prompt", "negative_prompt", "model_options"}):
            raise ValueError("unknown_animation_field")
        key = check_key(body["animation_key"])
        portrait_key = check_key(body["portrait_key"])
        portrait = _character_record("portraits", portrait_key)
        if portrait.get("status") != "ready":
            raise ApiError(409, "portrait_not_ready")
        if (CHARACTER_ROOT / "animation-requests" / (key + ".json")).exists():
            raise ApiError(409, "animation_key_exists")
        profile = json.loads(CHARACTER_PROFILE.read_text(encoding="utf-8"))
        defaults = {"profile_id": profile["profile_id"], "width": 320, "height": 576,
                    "frames": 33, "fps": 8, "steps": 8, "seed": 42, "timeout_seconds": 1800}
        options = body.get("model_options", {})
        if not isinstance(options, dict):
            raise ValueError("invalid_model_options")
        data = {"schema_version": 1, "animation_key": key, "portrait_key": portrait_key,
                "title": body["title"], "planning": body["planning"], "prompt": body["prompt"],
                "negative_prompt": body.get("negative_prompt", ""),
                "model_options": defaults | options, "created_at": character_now()}
        create_character_request("animation-requests", data)
        return JSONResponse({"animation_key": key, "portrait_key": portrait_key, "phase": "queued"}, status_code=202)
    except (KeyError, ValueError, TypeError) as exc:
        raise ApiError(422, "invalid_animation_request") from exc
    except FileExistsError as exc:
        raise ApiError(409, "animation_key_exists") from exc


def _character_key(request: Request, name: str) -> str:
    try:
        return check_key(request.path_params[name])
    except ValueError as exc:
        raise ApiError(400, "invalid_identifier") from exc


def _character_record(kind: str, key: str) -> dict:
    path = CHARACTER_ROOT / kind / (key + ".json")
    if not path.is_file() or path.is_symlink():
        raise ApiError(404, "character_record_not_found")
    return json.loads(path.read_text(encoding="utf-8"))


async def portraits(request: Request) -> Response:
    items = []
    for path in sorted((CHARACTER_ROOT / "portraits").glob("*.json")):
        if path.is_symlink():
            continue
        try:
            key = check_key(path.stem)
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("portrait_key") == key and data.get("status") == "ready":
                items.append({"portrait_key": key, "title": data["title"], "registered_at": data["registered_at"],
                              "image_url": f"/api/characters/portraits/{key}/image",
                              "animation_count": sum(1 for p in (CHARACTER_ROOT / "animations").glob("*.json") if json.loads(p.read_text(encoding="utf-8")).get("portrait_key") == key)})
        except (ValueError, KeyError, OSError):
            continue
    return JSONResponse({"portraits": items})


async def portrait_detail(request: Request) -> Response:
    key = _character_key(request, "portrait_key")
    data = _character_record("portraits", key)
    if data.get("portrait_key") != key:
        raise ApiError(409, "portrait_record_mismatch")
    animations = []
    for path in sorted((CHARACTER_ROOT / "animations").glob("*.json")):
        if path.is_symlink():
            continue
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
            if item.get("portrait_key") == key and item.get("animation_key") == path.stem:
                animations.append({"animation_key": path.stem, "title": item["title"], "status": item["status"],
                                   "updated_at": item.get("updated_at"),
                                   "video_url": f"/api/characters/animations/{path.stem}/video" if item["status"] == "completed" else None})
        except (ValueError, KeyError, OSError):
            continue
    return JSONResponse({"portrait_key": key, "title": data["title"], "planning": data["planning"], "prompt": data["prompt"],
                         "image_url": f"/api/characters/portraits/{key}/image", "animations": animations})


async def portrait_image(request: Request) -> Response:
    key = _character_key(request, "portrait_key")
    data = _character_record("portraits", key)
    path = CHARACTER_ROOT / "portrait-assets" / (key + ".png")
    if data.get("portrait_key") != key or not path.is_file() or path.is_symlink() or sha256(path) != data.get("asset_sha256"):
        raise ApiError(409, "portrait_asset_invalid")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-store"})


async def animation_detail(request: Request) -> Response:
    key = _character_key(request, "animation_key")
    data = _character_record("animations", key)
    if data.get("animation_key") != key:
        raise ApiError(409, "animation_record_mismatch")
    return JSONResponse({k: data.get(k) for k in ("animation_key", "portrait_key", "title", "planning", "prompt", "negative_prompt", "model_options", "status", "error", "created_at", "updated_at")} |
                        {"video_url": f"/api/characters/animations/{key}/video" if data.get("status") == "completed" else None})


async def animation_video(request: Request) -> Response:
    key = _character_key(request, "animation_key")
    data = _character_record("animations", key)
    job_id = data.get("job_id", "")
    if data.get("status") != "completed" or not TOKEN.fullmatch(job_id):
        raise ApiError(404, "animation_video_not_ready")
    path = _root(request) / (job_id + ".mp4")
    if not path.is_file() or path.is_symlink() or path.resolve() != Path(data.get("artifact", {}).get("path", "")).resolve() or sha256(path) != data["artifact"]["sha256"]:
        raise ApiError(409, "animation_video_invalid")
    return FileResponse(path, media_type="video/mp4", headers={"Cache-Control": "no-store"})


async def upload(request: Request) -> Response:
    content_length = request.headers.get("content-length")
    if content_length is not None and (not content_length.isdecimal()):
        raise ApiError(400, "invalid_request")
    body_limit = MAX_UPLOAD_BYTES + MAX_MULTIPART_OVERHEAD_BYTES
    if content_length is not None and int(content_length) > body_limit:
        raise ApiError(413, "upload_too_large")
    project = _project(request)
    receive = request._receive
    received = 0

    async def bounded_receive():
        nonlocal received
        message = await receive()
        if message["type"] == "http.request":
            received += len(message.get("body", b""))
            if received > body_limit:
                raise ApiError(413, "upload_too_large")
        return message

    request._receive = bounded_receive
    try:
        form = await request.form(max_files=1, max_fields=0)
    except HTTPException as exc:
        raise ApiError(400, "invalid_request") from exc
    file = form.get("file")
    if file is None or not hasattr(file, "read"):
        await form.close()
        raise ApiError(400, "file_required")
    if not str(file.filename or "").lower().endswith(".mp4"):
        await form.close()
        raise ApiError(415, "mp4_required")
    directory = project / "uploads"
    directory.mkdir(parents=True, exist_ok=True)
    upload_id = uuid.uuid4().hex
    destination = confined(directory / (upload_id + ".mp4"), _root(request))
    temp = destination.with_name("." + upload_id + ".part.mp4")
    size = 0
    try:
        with temp.open("xb") as stream:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise ApiError(413, "upload_too_large")
                stream.write(chunk)
            stream.flush()
            os.fsync(stream.fileno())
        with temp.open("rb") as stream:
            header = stream.read(12)
        if len(header) < 12 or header[4:8] != b"ftyp":
            raise ApiError(415, "invalid_mp4_content")
        _frame_count(temp)
        os.rename(temp, destination)
    finally:
        await form.close()
        temp.unlink(missing_ok=True)
    return JSONResponse({"upload_id": upload_id, "sha256": sha256(destination), "size_bytes": size,
                         "extract_url": f"/api/projects/{request.path_params['project']}/extractions"}, status_code=201)


async def reference(request: Request) -> Response:
    body = await _json(request)
    if set(body) != {"job_id"}:
        raise ApiError(400, "invalid_request")
    job_id = _id(body["job_id"], True)
    job_path = Path(request.app.state.job_root) / (job_id + ".json")
    try:
        job_path = confined(job_path, Path(request.app.state.job_root), True)
        job = json.loads(job_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        raise ApiError(404, "job_not_found")
    if job.get("status") != "completed" or job.get("job_id") != job_id:
        raise ApiError(409, "job_not_complete")
    source = _root(request) / (job_id + ".mp4")
    if not source.is_file() or Path(job.get("output_path", "")).resolve() != source.resolve():
        raise ApiError(409, "job_output_invalid")
    source = confined(source, _root(request), True)
    if source.stat().st_size > MAX_UPLOAD_BYTES:
        raise ApiError(413, "upload_too_large")
    with source.open("rb") as stream:
        if stream.read(12)[4:8] != b"ftyp":
            raise ApiError(415, "invalid_mp4_content")
    _frame_count(source)
    directory = _project(request) / "uploads"
    directory.mkdir(parents=True, exist_ok=True)
    upload_id = uuid.uuid4().hex
    destination = confined(directory / (upload_id + ".mp4"), _root(request))
    temp = destination.with_name("." + upload_id + ".part.mp4")
    try:
        with source.open("rb") as input_stream, temp.open("xb") as output_stream:
            shutil.copyfileobj(input_stream, output_stream, 1024 * 1024)
            output_stream.flush()
            os.fsync(output_stream.fileno())
        os.rename(temp, destination)
    finally:
        temp.unlink(missing_ok=True)
    return JSONResponse({"upload_id": upload_id, "sha256": sha256(destination),
                         "size_bytes": destination.stat().st_size,
                         "extract_url": f"/api/projects/{request.path_params['project']}/extractions"}, status_code=201)


async def extract(request: Request) -> Response:
    body = await _json(request)
    if set(body) != {"upload_id", "target_n"}:
        raise ApiError(400, "invalid_request")
    target_n = body["target_n"]
    if type(target_n) is not int or not 1 <= target_n <= MAX_TARGET_N:
        raise ApiError(400, "invalid_target_n")
    source = _source(request, body["upload_id"])
    _frame_count(source)
    extraction_id = uuid.uuid4().hex
    output = confined(_project(request) / "extractions" / extraction_id, _root(request))
    try:
        manifest = extract_candidates(source, output, target_n, _root(request), _root(request))
    except Exception as exc:
        raise ApiError(422, "extraction_failed") from exc
    request.path_params["extraction_id"] = extraction_id
    return JSONResponse(_public_manifest(request, manifest), status_code=201)


async def get_manifest(request: Request) -> Response:
    _, manifest = _manifest(request)
    return JSONResponse(_public_manifest(request, manifest))


def _frame(request: Request) -> Path:
    directory, manifest = _manifest(request)
    candidate_id = _id(request.path_params["candidate_id"])
    if candidate_id not in {item["candidate_id"] for item in manifest["candidates"]}:
        raise ApiError(404, "candidate_not_found")
    raw_path = directory / (candidate_id + ".png")
    _no_links(raw_path, _root(request))
    path = confined(raw_path, _root(request))
    if not path.is_file():
        raise ApiError(404, "candidate_not_found")
    return path


async def preview(request: Request) -> Response:
    return FileResponse(_frame(request), media_type="image/png", headers={"Cache-Control": "no-store"})


async def download(request: Request) -> Response:
    path = _frame(request)
    return FileResponse(path, media_type="image/png", filename=path.name)


async def selection(request: Request) -> Response:
    directory, manifest = _manifest(request)
    if request.method == "GET":
        path = directory / "selection.json"
        if not path.is_file():
            raise ApiError(404, "selection_not_found")
        saved = json.loads(path.read_text(encoding="utf-8"))
        return JSONResponse({"selected_candidate_ids": saved["selected_candidate_ids"],
                             "automatic_final_selection": False,
                             "candidate_manifest_sha256": saved["candidate_manifest_sha256"]})
    body = await _json(request)
    ids = body.get("selected_candidate_ids")
    if not isinstance(ids, list) or not all(isinstance(item, str) for item in ids):
        raise ApiError(400, "invalid_selection")
    path = directory / "selection.json"
    with (directory / ".selection.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if path.exists():
            raise ApiError(409, "selection_exists")
        try:
            saved = selection_manifest(directory / "candidates.json", ids, path, _root(request), _root(request))
        except ValueError as exc:
            raise ApiError(400, "invalid_selection") from exc
    return JSONResponse({"selected_candidate_ids": saved["selected_candidate_ids"],
                         "automatic_final_selection": False,
                         "candidate_manifest_sha256": saved["candidate_manifest_sha256"]}, status_code=201)


async def download_zip(request: Request) -> Response:
    directory, manifest = _manifest(request)
    selected = request.query_params.get("selected") == "true"
    items = manifest["candidates"]
    if selected:
        selection_path = directory / "selection.json"
        if not selection_path.is_file():
            raise ApiError(404, "selection_not_found")
        ids = set(json.loads(selection_path.read_text())["selected_candidate_ids"])
        items = [item for item in items if item["candidate_id"] in ids]
    stream = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)
    try:
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as archive:
            archive.writestr("candidates.json", json.dumps(_public_manifest(request, manifest)))
            for item in items:
                raw_path = directory / (item["candidate_id"] + ".png")
                _no_links(raw_path, _root(request))
                path = confined(raw_path, _root(request))
                if not path.is_file():
                    raise ApiError(404, "candidate_not_found")
                if sha256(path) != item["sha256"]:
                    raise ApiError(409, "candidate_integrity_failed")
                archive.write(path, "candidates/" + path.name)
        stream.seek(0)
    except Exception:
        stream.close()
        raise

    def chunks():
        try:
            while chunk := stream.read(1024 * 1024):
                yield chunk
        finally:
            stream.close()

    return StreamingResponse(chunks(), media_type="application/zip",
                             headers={"Content-Disposition": 'attachment; filename="candidates.zip"'})


async def api_error(request: Request, exc: ApiError) -> Response:
    return JSONResponse({"error": {"code": exc.code}}, status_code=exc.status)


def create_app(output_root: Path = OUTPUT_DIR, job_root: Path = JOB_DIR) -> Starlette:
    app = Starlette(routes=[
        Route("/api/health", health),
        Route("/api/characters/schemas", character_schemas),
        Route("/api/characters/portrait-requests", submit_portrait, methods=["POST"]),
        Route("/api/characters/animation-requests", submit_animation, methods=["POST"]),
        Route("/api/characters/portraits", portraits),
        Route("/api/characters/portraits/{portrait_key}", portrait_detail),
        Route("/api/characters/portraits/{portrait_key}/image", portrait_image),
        Route("/api/characters/animations/{animation_key}", animation_detail),
        Route("/api/characters/animations/{animation_key}/video", animation_video),
        Route("/api/projects/{project}/uploads", upload, methods=["POST"]),
        Route("/api/projects/{project}/uploads/references", reference, methods=["POST"]),
        Route("/api/projects/{project}/extractions", extract, methods=["POST"]),
        Route("/api/projects/{project}/extractions/{extraction_id}", get_manifest),
        Route("/api/projects/{project}/extractions/{extraction_id}/selection", selection, methods=["GET", "POST"]),
        Route("/api/projects/{project}/extractions/{extraction_id}/download.zip", download_zip),
        Route("/api/projects/{project}/extractions/{extraction_id}/candidates/{candidate_id}/preview", preview),
        Route("/api/projects/{project}/extractions/{extraction_id}/candidates/{candidate_id}/download", download),
    ], exception_handlers={ApiError: api_error})
    app.state.output_root = Path(output_root)
    app.state.job_root = Path(job_root)
    return app


app = create_app()


def main() -> None:
    import uvicorn
    uvicorn.run("videoforge_mcp.http:app", host="127.0.0.1", port=8001)
