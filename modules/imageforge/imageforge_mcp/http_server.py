from __future__ import annotations

import io
import json
import re
import statistics
import uuid
import zipfile
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from PIL import Image, UnidentifiedImageError

from imageforge_mcp import storage
from .image_ops import (
    Component,
    crop_component,
    find_components,
    remove_background,
    remove_background_from_corners,
    select_connected_layer,
)


ROOT = Path(__file__).resolve().parent
DOWNLOADS_ROOT = storage.downloads_root()
GENERATED_IMAGE_ROOT = storage.generated_root()
MAX_UPLOAD_BYTES = 50 * 1024 * 1024


class ImageSession:
    def __init__(self, image: Image.Image, filename: str, output_dir: Path) -> None:
        self.image = image
        self.filename = filename
        self.output_dir = output_dir
        self.history: list[Image.Image] = []
        self.components: list[Component] = []
        self.component_layers: list[Image.Image] = []
        self.component_offsets: list[tuple[int, int]] = []
        self.component_scales: list[float] = []
        self.layers: list[tuple[Image.Image, Component]] = []


class RemoveRequest(BaseModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    tolerance: float = Field(default=5, ge=0, le=100)
    contiguous: bool = True
    feather: float = Field(default=1, ge=0, le=25)


class ComponentsRequest(BaseModel):
    alpha_threshold: int = Field(default=120, ge=1, le=255)
    min_pixels: int = Field(default=2000, ge=1)


class AutoSelectRequest(BaseModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    alpha_threshold: int = Field(default=120, ge=1, le=255)


class AutoBackgroundRequest(BaseModel):
    tolerance: float = Field(default=5, ge=0, le=100)
    feather: float = Field(default=1, ge=0, le=25)
    sample_radius: int = Field(default=2, ge=0, le=20)


class MoveObjectRequest(BaseModel):
    dx: int = Field(default=0, ge=-1000, le=1000)
    dy: int = Field(default=0, ge=-1000, le=1000)


class ScaleObjectRequest(BaseModel):
    scale: float = Field(default=1.0, ge=0.1, le=1.0)


class FrameExportRequest(BaseModel):
    frame_height: int = Field(default=340, ge=16, le=4096)
    horizontal_margin: int = Field(default=120, ge=0, le=2000)
    bottom_padding: int = Field(default=20, ge=0, le=1000)


class GeneratedFrameOpenRequest(BaseModel):
    asset: str = Field(min_length=1, max_length=100)
    action: str = Field(min_length=1, max_length=100)
    filename: str = Field(pattern=r"^frame[-_]\d+\.png$")


app = FastAPI(
    title="ImageForge API",
    version="1.0.0",
    description="ImageForge MCP-compatible HTTP API for background removal, object extraction, and sprite-frame editing.",
)


@app.middleware('http')
async def reject_cross_origin_writes(request: Request, call_next):
    if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
        origin = request.headers.get('origin')
        proxy_origin = request.headers.get('x-forge-public-origin', '')
        expected = proxy_origin.rstrip('/') if proxy_origin.startswith(('http://', 'https://')) else str(request.base_url).rstrip('/')
        if origin and origin.rstrip('/') != expected:
            return JSONResponse(status_code=403, content={'detail': 'cross_origin_write_forbidden'})
    return await call_next(request)


@app.exception_handler(storage.StorageUnavailable)
async def storage_unavailable_handler(_request, exc: storage.StorageUnavailable) -> JSONResponse:
    return JSONResponse(status_code=503, content={"error": "STORAGE_UNAVAILABLE", "detail": str(exc)})


@app.exception_handler(storage.StorageConflict)
async def storage_conflict_handler(_request, exc: storage.StorageConflict) -> JSONResponse:
    return JSONResponse(status_code=409, content={"error": "STORAGE_CONFLICT", "detail": str(exc)})
sessions: dict[str, ImageSession] = {}
background_editor_targets: dict[str, Path] = {}
EXTERNAL_BACKGROUND_TOLERANCES = (4, 3, 2, 1)
EXTERNAL_ALPHA_THRESHOLDS = (150, 140, 130, 120, 110, 100)


def get_session(session_id: str) -> ImageSession:
    session = sessions.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Image session not found")
    return session


def png_response(image: Image.Image, filename: str) -> Response:
    output = io.BytesIO()
    image.save(output, "PNG")
    return Response(
        content=output.getvalue(),
        media_type="image/png",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


def session_folder(filename: str, session_id: str) -> Path:
    return storage.new_job(session_id, "original")


def saved_payload(path: Path) -> dict[str, object]:
    return {"ok": True, "path": str(path), "relative_path": str(path.relative_to(storage.shared_root()))}


def exact_component_layers(
    image: Image.Image, components: list[Component], alpha_threshold: int
) -> list[Image.Image]:
    return [
        select_connected_layer(image, (item.seed_x, item.seed_y), alpha_threshold)[0]
        for item in components
    ]


def safe_name(value: str, fallback: str) -> str:
    return re.sub(r"[^\w.-]+", "_", value, flags=re.UNICODE).strip("._")[:80] or fallback


def package_directory(asset_name: str, action_name: str, job_id: str) -> Path:
    asset = f"{safe_name(asset_name, 'image')}_{job_id[:8]}"
    adopted = storage.new_job(job_id, "adopted")
    asset_dir = storage.new_directory(adopted, asset)
    directory = storage.new_directory(asset_dir, safe_name(action_name, "Frames"))
    return directory


def frame_zip(frames: list[Image.Image], manifest: dict[str, object]) -> io.BytesIO:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        for index, frame in enumerate(frames):
            encoded = io.BytesIO()
            frame.save(encoded, "PNG")
            archive.writestr(f"frame-{index}.png", encoded.getvalue())
    output.seek(0)
    return output


def generated_action_payload(asset_dir: Path, action_dir: Path) -> dict[str, object]:
    frames = sorted(
        (path for path in action_dir.glob("frame*.png") if re.fullmatch(r"frame[-_]\d+\.png", path.name)
         and storage.verified_file(path)),
        key=lambda path: int(re.search(r"\d+", path.stem).group()),
    )
    manifest: dict[str, object] = {}
    for name in ("transform-manifest.json", "frame-manifest.json"):
        manifest_path = action_dir / name
        if storage.verified_file(manifest_path):
            try:
                manifest = json.loads(storage.read_verified_bytes(manifest_path).decode("utf-8"))
            except (json.JSONDecodeError, OSError):
                manifest = {}
            break
    canvas = manifest.get("canvas")
    if (not isinstance(canvas, list) or len(canvas) != 2) and frames:
        with Image.open(io.BytesIO(storage.read_verified_bytes(frames[0]))) as frame:
            canvas = [frame.width, frame.height]
    return {
        "asset": asset_dir.name,
        "action": action_dir.name,
        "frame_count": len(frames),
        "canvas": canvas or [0, 0],
        "transforms": manifest.get("transforms", []),
        "frames": [
            {
                "filename": frame.name,
                "url": f"/api/external/generated-images/{asset_dir.name}/{action_dir.name}/{frame.name}",
            }
            for frame in frames
        ],
    }


def generated_action_dirs() -> list[tuple[Path, Path, str]]:
    found: list[tuple[Path, Path, str]] = []
    roots = []
    if GENERATED_IMAGE_ROOT.is_dir() and not GENERATED_IMAGE_ROOT.is_symlink():
        roots.extend((job / "adopted", "shared") for job in GENERATED_IMAGE_ROOT.iterdir()
                     if job.is_dir() and not job.is_symlink())
    roots.append((storage.LEGACY_GENERATED, "legacy"))
    for root, origin in roots:
        if not root.is_dir() or root.is_symlink():
            continue
        for asset in root.iterdir():
            if not asset.is_dir() or asset.is_symlink():
                continue
            for action in asset.iterdir():
                if action.is_dir() and not action.is_symlink():
                    found.append((asset, action, origin))
    return found


def resolve_generated_frame(asset: str, action: str, filename: str) -> Path:
    if any(safe_name(value, "") != value for value in (asset, action)) or not re.fullmatch(r"frame[-_]\d+\.png", filename):
        raise HTTPException(status_code=404, detail="Generated frame not found")
    candidates = (action_dir / filename for asset_dir, action_dir, _ in generated_action_dirs()
                  if asset_dir.name == asset and action_dir.name == action)
    found = [path for path in candidates if storage.verified_file(path)]
    if len(found) > 1 and found[0].read_bytes() != found[1].read_bytes():
        raise HTTPException(status_code=409, detail="Generated frame conflicts with legacy data")
    if not found:
        raise HTTPException(status_code=404, detail="Generated frame not found")
    return found[0]


def spatial_order(components: list[Component]) -> list[Component]:
    """Order sprite-sheet objects by rows, then left-to-right within each row."""
    return [item for row in component_rows(components) for item in row]


def component_rows(components: list[Component]) -> list[list[Component]]:
    """Group components into visual rows and order each row from left to right."""
    if not components:
        return []
    row_tolerance = max(8.0, statistics.median(item.height for item in components) * 0.55)
    remaining = sorted(components, key=lambda item: item.y + item.height / 2)
    rows: list[list[Component]] = []
    while remaining:
        anchor = remaining[0].y + remaining[0].height / 2
        row = [item for item in remaining if abs((item.y + item.height / 2) - anchor) <= row_tolerance]
        rows.append(sorted(row, key=lambda item: item.x))
        row_ids = {id(item) for item in row}
        remaining = [item for item in remaining if id(item) not in row_ids]
    return rows


def render_source_height_frames(
    image_width: int,
    image_height: int,
    rows: list[list[Component]],
    layers: list[Image.Image],
    horizontal_padding: int = 20,
) -> tuple[list[Image.Image], int]:
    """Center layers on a shared canvas sized from the widest object plus side padding."""
    if not rows:
        return [], image_height
    row_count = len(rows)
    frame_height = (image_height + row_count - 1) // row_count
    frame_width = max(layer.width for layer in layers) + horizontal_padding * 2
    frames: list[Image.Image] = []
    layer_index = 0
    for row_index, row in enumerate(rows):
        row_origin_y = row_index * frame_height
        for component in row:
            layer = layers[layer_index]
            frame = Image.new("RGBA", (frame_width, frame_height), (255, 255, 255, 0))
            x = (frame_width - layer.width) // 2
            frame.alpha_composite(layer, (x, component.y - row_origin_y))
            frames.append(frame)
            layer_index += 1
    return frames, frame_height


def render_fixed_frames(session: ImageSession, request: FrameExportRequest) -> list[Image.Image]:
    if not session.component_layers:
        raise HTTPException(status_code=409, detail="Run component detection first")
    scales = session.component_scales or [1.0] * len(session.component_layers)
    frame_width = max(round(layer.width * scales[index]) for index, layer in enumerate(session.component_layers)) + request.horizontal_margin * 2
    offsets = session.component_offsets or [(0, 0)] * len(session.component_layers)
    frames: list[Image.Image] = []
    for index, layer in enumerate(session.component_layers):
        frame = Image.new("RGBA", (frame_width, request.frame_height), (255, 255, 255, 0))
        scale = scales[index]
        if scale != 1.0:
            layer = layer.resize(
                (max(1, round(layer.width * scale)), max(1, round(layer.height * scale))),
                Image.Resampling.LANCZOS,
            )
        offset_x, offset_y = offsets[index]
        x = (frame_width - layer.width) // 2 + offset_x
        y = request.frame_height - request.bottom_padding - layer.height + offset_y
        frame.alpha_composite(layer, (x, y))
        frames.append(frame)
    return frames


@app.get("/api/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/images", tags=["images"])
async def upload_image(file: UploadFile = File(...)) -> dict[str, object]:
    raw = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Image must be 50 MB or smaller")
    try:
        image = Image.open(io.BytesIO(raw)).convert("RGBA")
        image.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise HTTPException(status_code=415, detail="Unsupported or invalid image") from exc
    session_id = uuid.uuid4().hex
    filename = file.filename or "image.png"
    output_dir = session_folder(filename, session_id)
    storage.save_png(image, output_dir / "source.png")
    storage.commit(output_dir, origin="original", source=filename)
    sessions[session_id] = ImageSession(image, filename, output_dir)
    return {
        "session_id": session_id,
        "filename": filename,
        "width": image.width,
        "height": image.height,
        "output_directory": str(output_dir),
    }


@app.post("/api/external/extract-frames", tags=["external"])
@app.post("/api/external/extract-objects", tags=["external"], deprecated=True)
async def external_extract_objects(
    file: UploadFile = File(..., description="PNG sprite sheet"),
    expected_object_count: int = Form(..., ge=1, le=1000),
    output_mode: str = Form("save", pattern="^(save|direct)$"),
    asset_name: str = Form(""),
    action_name: str = Form("Frames"),
    horizontal_padding: int = Form(20, ge=0, le=2000),
) -> Response:
    """Find the first tolerance/alpha-threshold pair that yields the expected object count."""
    raw = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="PNG must be 50 MB or smaller")
    try:
        opened = Image.open(io.BytesIO(raw))
        if opened.format != "PNG":
            raise HTTPException(status_code=415, detail="Only PNG input is supported")
        source = opened.convert("RGBA")
        source.load()
    except HTTPException:
        raise
    except (UnidentifiedImageError, OSError) as exc:
        raise HTTPException(status_code=415, detail="Unsupported or invalid PNG") from exc

    job_id = uuid.uuid4().hex
    filename = file.filename or "input.png"
    attempts: list[dict[str, int]] = []

    for tolerance in EXTERNAL_BACKGROUND_TOLERANCES:
        transparent = remove_background_from_corners(
            source, tolerance=tolerance, feather=1, sample_radius=2
        )
        for alpha_threshold in EXTERNAL_ALPHA_THRESHOLDS:
            rows = component_rows(
                find_components(transparent, alpha_threshold=alpha_threshold, min_pixels=2000)
            )
            components = [item for row in rows for item in row]
            attempts.append(
                {
                    "background_tolerance": tolerance,
                    "alpha_threshold": alpha_threshold,
                    "detected_object_count": len(components),
                }
            )
            if len(components) != expected_object_count:
                continue

            resolved_asset_name = asset_name or Path(filename).stem
            png_list: list[dict[str, object]] = []
            layers = exact_component_layers(transparent, components, alpha_threshold)
            frames, frame_height = render_source_height_frames(
                source.width, source.height, rows, layers, horizontal_padding
            )
            manifest = {
                "asset": safe_name(resolved_asset_name, "image"),
                "action": safe_name(action_name, "Frames"),
                "canvas": [frames[0].width, frame_height],
                "frameCount": len(frames),
                "sourceSize": [source.width, source.height],
                "rowCount": len(rows),
                "selectedSettings": {"backgroundTolerance": tolerance, "alphaThreshold": alpha_threshold},
                "horizontalPadding": horizontal_padding,
            }
            if output_mode == "direct":
                archive = frame_zip(frames, manifest)
                return StreamingResponse(
                    archive,
                    media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{safe_name(resolved_asset_name, "frames")}.zip"'},
                )

            output_dir = package_directory(resolved_asset_name, action_name, job_id)
            storage.save_png(source, output_dir / "imagegen-source.png")
            storage.save_png(transparent, output_dir / "background-removed.png")
            for index, frame in enumerate(frames):
                name = f"frame-{index}.png"
                path = output_dir / name
                storage.save_png(frame, path)
                png_list.append({
                    "index": index,
                    "filename": name,
                    "width": frame.width,
                    "height": frame.height,
                    "path": str(path),
                })
            storage.save_json(output_dir / "frame-manifest.json", manifest)
            storage.commit(output_dir, origin="adopted", source=filename, settings=manifest)
            return JSONResponse(
                {
                    "ok": True,
                    "job_id": job_id,
                    "expected_object_count": expected_object_count,
                    "object_count": len(png_list),
                    "selected_settings": {
                        "background_tolerance": tolerance,
                        "alpha_threshold": alpha_threshold,
                    },
                    "layout": {
                        "row_count": len(rows),
                        "source_height": source.height,
                        "frame_height": frame_height,
                    },
                    "png_list": png_list,
                    "output_directory": str(output_dir),
                    "attempts": attempts,
                }
            )

    return JSONResponse(
        status_code=422,
        content={
            "ok": False,
            "job_id": job_id,
            "error": "EXPECTED_OBJECT_COUNT_NOT_FOUND",
            "expected_object_count": expected_object_count,
            "png_list": [],
            "attempts": attempts,
        },
    )


@app.post("/api/external/transform-frames", tags=["external"])
async def external_transform_frames(
    files: list[UploadFile] = File(..., description="Ordered transparent PNG frames"),
    transforms: str = Form("[]", description='JSON list: [{"x":0,"y":0,"scale":1.0}]'),
    canvas_width: int = Form(..., ge=1, le=8192),
    canvas_height: int = Form(..., ge=1, le=8192),
    output_mode: str = Form("save", pattern="^(save|direct|overwrite)$"),
    asset_name: str = Form("frames"),
    action_name: str = Form("Frames"),
    overwrite_asset: str = Form(""),
    overwrite_action: str = Form(""),
    source_filenames: str = Form("[]"),
) -> Response:
    """Resize and move already-separated frames without performing background removal."""
    try:
        specs = json.loads(transforms)
        if not isinstance(specs, list):
            raise ValueError
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="transforms must be a JSON list") from exc
    source_frames: list[Image.Image] = []
    for upload in files:
        raw = await upload.read(MAX_UPLOAD_BYTES + 1)
        try:
            opened = Image.open(io.BytesIO(raw))
            if opened.format != "PNG":
                raise ValueError
            frame = opened.convert("RGBA")
            frame.load()
            source_frames.append(frame)
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise HTTPException(status_code=415, detail="Every frame must be a valid PNG") from exc
    output_frames: list[Image.Image] = []
    applied: list[dict[str, float | int]] = []
    for index, source_frame in enumerate(source_frames):
        spec = specs[index] if index < len(specs) and isinstance(specs[index], dict) else {}
        x, y, scale = int(spec.get("x", 0)), int(spec.get("y", 0)), float(spec.get("scale", 1.0))
        if not 0.05 <= scale <= 8.0:
            raise HTTPException(status_code=422, detail=f"Frame {index} scale must be 0.05..8.0")
        width, height = max(1, round(source_frame.width * scale)), max(1, round(source_frame.height * scale))
        resized = source_frame.resize((width, height), Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", (canvas_width, canvas_height), (255, 255, 255, 0))
        canvas.alpha_composite(resized, ((canvas_width - width) // 2 + x, (canvas_height - height) // 2 + y))
        output_frames.append(canvas)
        applied.append({"x": x, "y": y, "scale": scale})
    manifest = {
        "asset": safe_name(asset_name, "frames"),
        "action": safe_name(action_name, "Frames"),
        "canvas": [canvas_width, canvas_height],
        "frameCount": len(output_frames),
        "appliedTransforms": applied,
        "transforms": [{"x": 0, "y": 0, "scale": 1.0} for _ in output_frames],
    }
    if output_mode == "direct":
        return StreamingResponse(frame_zip(output_frames, manifest), media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{safe_name(asset_name, "frames")}-transformed.zip"'})
    if output_mode == "overwrite":
        try:
            filenames = json.loads(source_filenames)
            if not isinstance(filenames, list) or len(filenames) != len(output_frames):
                raise ValueError
        except (json.JSONDecodeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail="source_filenames must match every uploaded frame") from exc
        targets = [resolve_generated_frame(overwrite_asset, overwrite_action, str(name)) for name in filenames]
        if len({target.parent for target in targets}) != 1:
            raise HTTPException(status_code=422, detail="Frames must belong to one action folder")
        asset_name, action_name = overwrite_asset, overwrite_action
        manifest["asset"], manifest["action"] = overwrite_asset, overwrite_action
    job_id = uuid.uuid4().hex
    output_dir = package_directory(asset_name, action_name, job_id)
    for index, frame in enumerate(output_frames):
        storage.save_png(frame, output_dir / f"frame-{index}.png")
    storage.save_json(output_dir / "transform-manifest.json", manifest)
    storage.commit(output_dir, origin="adopted",
                   source=",".join(str(t) for t in targets) if output_mode == "overwrite" else None,
                   settings=manifest)
    return JSONResponse({"ok": True, "job_id": job_id, "overwritten": False,
                         "versioned_from": output_mode == "overwrite", "output_directory": str(output_dir),
                         "canvas": [canvas_width, canvas_height],
                         "frames": [str(output_dir / f"frame-{index}.png") for index in range(len(output_frames))],
                         "transforms": applied})


@app.get("/api/external/generated-images", tags=["external"])
def list_generated_images() -> dict[str, object]:
    """List editable generated_image asset/action frame folders."""
    assets: list[dict[str, object]] = []
    by_name: dict[str, dict[str, object]] = {}
    for asset_dir, action_dir, origin in generated_action_dirs():
        action = generated_action_payload(asset_dir, action_dir)
        if not action["frame_count"]:
            continue
        entry = by_name.setdefault(asset_dir.name, {"name": asset_dir.name, "actions": [], "origin": origin})
        if any(existing["action"] == action_dir.name for existing in entry["actions"]):
            raise HTTPException(status_code=409, detail="Generated asset conflicts with legacy data")
        entry["actions"].append(action)
    assets = list(by_name.values())
    return {"root": str(GENERATED_IMAGE_ROOT), "assets": assets}


@app.get("/api/external/generated-images/{asset}/{action}/{filename}", tags=["external"])
def get_generated_frame(asset: str, action: str, filename: str) -> Response:
    """Read one frame from a generated_image package without allowing path traversal."""
    path = resolve_generated_frame(asset, action, filename)
    return Response(content=storage.read_verified_bytes(path), media_type="image/png",
                    headers={"Content-Disposition": f'inline; filename="{filename}"'})


@app.post("/api/external/background-editor/open", tags=["external"])
def open_generated_background_editor(request: GeneratedFrameOpenRequest) -> dict[str, object]:
    """Open one generated frame in an isolated edit session without changing its file."""
    path = resolve_generated_frame(request.asset, request.action, request.filename)
    try:
        with Image.open(io.BytesIO(storage.read_verified_bytes(path))) as opened:
            image = opened.convert("RGBA")
            image.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise HTTPException(status_code=415, detail="Generated frame is not a readable PNG") from exc
    session_id = uuid.uuid4().hex
    sessions[session_id] = ImageSession(image, request.filename, path.parent)
    background_editor_targets[session_id] = path
    return {
        "session_id": session_id,
        "asset": request.asset,
        "action": request.action,
        "filename": request.filename,
        "width": image.width,
        "height": image.height,
        "preview_url": f"/api/images/{session_id}",
        "target_path": str(path),
    }


@app.post("/api/external/background-editor/{session_id}/overwrite", tags=["external"])
def overwrite_generated_background_frame(session_id: str) -> dict[str, object]:
    """Publish the edited frame as a new immutable generated package."""
    session = get_session(session_id)
    path = background_editor_targets.get(session_id)
    if path is None:
        raise HTTPException(status_code=404, detail="Background editor session not found")
    directory = package_directory(path.parent.parent.name, path.parent.name, uuid.uuid4().hex)
    output = directory / path.name
    storage.save_png(session.image, output)
    storage.save_json(directory / "transform-manifest.json", {
        "asset": directory.parent.name, "action": directory.name,
        "canvas": [session.image.width, session.image.height], "frameCount": 1,
        "parent_path": str(path),
    })
    storage.commit(directory, origin="adopted", source=str(path),
                   settings={"canvas": [session.image.width, session.image.height]})
    return {"ok": True, "path": str(output), "width": session.image.width,
            "height": session.image.height, "overwritten": False}


@app.get("/api/images/{session_id}", tags=["images"])
def get_image(session_id: str) -> Response:
    session = get_session(session_id)
    return png_response(session.image, "result.png")


@app.post("/api/images/{session_id}/save", tags=["downloads"])
def save_result(session_id: str) -> dict[str, object]:
    session = get_session(session_id)
    directory = storage.new_job(uuid.uuid4().hex, "intermediate")
    path = directory / "result.png"
    storage.save_png(session.image, path)
    storage.commit(directory, origin="intermediate", source=session.filename)
    return saved_payload(path)


@app.post("/api/images/{session_id}/remove-background", tags=["editing"])
def remove_image_background(session_id: str, request: RemoveRequest) -> dict[str, object]:
    session = get_session(session_id)
    if request.x >= session.image.width or request.y >= session.image.height:
        raise HTTPException(status_code=422, detail="Selected point is outside the image")
    session.history.append(session.image.copy())
    session.image = remove_background(
        session.image,
        (request.x, request.y),
        request.tolerance,
        request.contiguous,
        request.feather,
    )
    session.components.clear()
    session.component_layers.clear()
    session.component_offsets.clear()
    session.component_scales.clear()
    return {"ok": True, "width": session.image.width, "height": session.image.height}


@app.post("/api/images/{session_id}/auto-remove-background", tags=["editing"])
def auto_remove_image_background(session_id: str, request: AutoBackgroundRequest) -> dict[str, object]:
    session = get_session(session_id)
    session.history.append(session.image.copy())
    session.image = remove_background_from_corners(
        session.image,
        request.tolerance,
        request.feather,
        request.sample_radius,
    )
    session.components.clear()
    session.component_layers.clear()
    session.component_offsets.clear()
    session.component_scales.clear()
    session.layers.clear()
    return {
        "ok": True,
        "samples": [
            {"x": 0, "y": 0},
            {"x": session.image.width - 1, "y": 0},
            {"x": 0, "y": session.image.height - 1},
            {"x": session.image.width - 1, "y": session.image.height - 1},
        ],
        "width": session.image.width,
        "height": session.image.height,
    }


@app.post("/api/images/{session_id}/undo", tags=["editing"])
def undo(session_id: str) -> dict[str, object]:
    session = get_session(session_id)
    if not session.history:
        return {"ok": False, "detail": "Nothing to undo"}
    session.image = session.history.pop()
    session.components.clear()
    session.component_layers.clear()
    session.component_offsets.clear()
    session.component_scales.clear()
    return {"ok": True}


@app.post("/api/images/{session_id}/components", tags=["components"])
def detect_components(session_id: str, request: ComponentsRequest) -> dict[str, object]:
    session = get_session(session_id)
    session.components = spatial_order(find_components(session.image, request.alpha_threshold, request.min_pixels))
    session.component_layers = exact_component_layers(
        session.image, session.components, request.alpha_threshold
    )
    session.component_offsets = [(0, 0)] * len(session.components)
    session.component_scales = [1.0] * len(session.components)
    return {
        "count": len(session.components),
        "components": [
            {**asdict(item), "url": f"/api/images/{session_id}/components/{index}"}
            for index, item in enumerate(session.components)
        ],
    }


@app.get("/api/images/{session_id}/components/{component_index}", tags=["components"])
def get_component(session_id: str, component_index: int) -> Response:
    session = get_session(session_id)
    if not 0 <= component_index < len(session.components):
        raise HTTPException(status_code=404, detail="Component not found; run component detection first")
    return png_response(session.component_layers[component_index], f"object_{component_index + 1:03d}.png")


@app.post("/api/images/{session_id}/save-components", tags=["downloads"])
def save_all_components(session_id: str, request: FrameExportRequest) -> dict[str, object]:
    session = get_session(session_id)
    if not session.components:
        raise HTTPException(status_code=409, detail="Run component detection first")
    folder = storage.new_job(uuid.uuid4().hex, "intermediate")
    paths: list[str] = []
    frames = render_fixed_frames(session, request)
    for index, frame in enumerate(frames, 1):
        path = folder / f"frame_{index:03d}.png"
        storage.save_png(frame, path)
        paths.append(str(path))
    storage.commit(folder, origin="intermediate", source=session.filename)
    return {
        "ok": True,
        "count": len(paths),
        "directory": str(folder),
        "paths": paths,
        "frame_width": frames[0].width,
        "frame_height": frames[0].height,
    }


@app.post("/api/images/{session_id}/animation/objects/{component_index}/move", tags=["animation"])
def move_animation_object(session_id: str, component_index: int, request: MoveObjectRequest) -> dict[str, object]:
    session = get_session(session_id)
    if not 0 <= component_index < len(session.component_layers):
        raise HTTPException(status_code=404, detail="Object not found")
    old_x, old_y = session.component_offsets[component_index]
    position = (old_x + request.dx, old_y + request.dy)
    session.component_offsets[component_index] = position
    return {"ok": True, "component_index": component_index, "offset_x": position[0], "offset_y": position[1]}


@app.post("/api/images/{session_id}/animation/objects/{component_index}/scale", tags=["animation"])
def scale_animation_object(session_id: str, component_index: int, request: ScaleObjectRequest) -> dict[str, object]:
    session = get_session(session_id)
    if not 0 <= component_index < len(session.component_layers):
        raise HTTPException(status_code=404, detail="Object not found")
    session.component_scales[component_index] = request.scale
    return {"ok": True, "component_index": component_index, "scale": request.scale}


@app.delete("/api/images/{session_id}/animation/objects/{component_index}", tags=["animation"])
def delete_animation_object(session_id: str, component_index: int) -> dict[str, object]:
    session = get_session(session_id)
    if not 0 <= component_index < len(session.component_layers):
        raise HTTPException(status_code=404, detail="Object not found")
    del session.components[component_index]
    del session.component_layers[component_index]
    del session.component_offsets[component_index]
    del session.component_scales[component_index]
    return {"ok": True, "deleted_index": component_index, "remaining": len(session.component_layers)}


@app.post("/api/images/{session_id}/layers/auto-select", tags=["layers"])
def auto_select_layer(session_id: str, request: AutoSelectRequest) -> dict[str, object]:
    session = get_session(session_id)
    if request.x >= session.image.width or request.y >= session.image.height:
        raise HTTPException(status_code=422, detail="Selected point is outside the image")
    try:
        layer, component = select_connected_layer(
            session.image,
            (request.x, request.y),
            request.alpha_threshold,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.layers.append((layer, component))
    layer_index = len(session.layers) - 1
    return {
        "layer_id": layer_index,
        "name": f"Layer {layer_index + 1}",
        "x": component.x,
        "y": component.y,
        "width": component.width,
        "height": component.height,
        "pixels": component.pixels,
        "url": f"/api/images/{session_id}/layers/{layer_index}",
    }


@app.get("/api/images/{session_id}/layers", tags=["layers"])
def list_layers(session_id: str) -> dict[str, object]:
    session = get_session(session_id)
    return {
        "count": len(session.layers),
        "layers": [
            {
                "layer_id": index,
                "name": f"Layer {index + 1}",
                "x": component.x,
                "y": component.y,
                "width": component.width,
                "height": component.height,
                "pixels": component.pixels,
                "url": f"/api/images/{session_id}/layers/{index}",
            }
            for index, (_, component) in enumerate(session.layers)
        ],
    }


@app.get("/api/images/{session_id}/layers/{layer_index}", tags=["layers"])
def get_layer(session_id: str, layer_index: int) -> Response:
    session = get_session(session_id)
    if not 0 <= layer_index < len(session.layers):
        raise HTTPException(status_code=404, detail="Layer not found")
    layer, _ = session.layers[layer_index]
    return png_response(layer, f"layer_{layer_index + 1:03d}.png")


@app.post("/api/images/{session_id}/layers/{layer_index}/save", tags=["downloads"])
def save_layer(session_id: str, layer_index: int) -> dict[str, object]:
    session = get_session(session_id)
    if not 0 <= layer_index < len(session.layers):
        raise HTTPException(status_code=404, detail="Layer not found")
    folder = storage.new_job(uuid.uuid4().hex, "intermediate")
    path = folder / f"layer_{layer_index + 1:03d}.png"
    storage.save_png(session.layers[layer_index][0], path)
    storage.commit(folder, origin="intermediate", source=session.filename)
    return saved_payload(path)


@app.post("/api/images/{session_id}/save-layers", tags=["downloads"])
def save_all_layers(session_id: str) -> dict[str, object]:
    session = get_session(session_id)
    if not session.layers:
        raise HTTPException(status_code=409, detail="No layers to save")
    folder = storage.new_job(uuid.uuid4().hex, "intermediate")
    paths: list[str] = []
    for index, (layer, _) in enumerate(session.layers, 1):
        path = folder / f"layer_{index:03d}.png"
        storage.save_png(layer, path)
        paths.append(str(path))
    storage.commit(folder, origin="intermediate", source=session.filename)
    return {"ok": True, "count": len(paths), "directory": str(folder), "paths": paths}


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(ROOT / "web" / "index.html")


app.mount("/web", StaticFiles(directory=ROOT / "web"), name="web")
