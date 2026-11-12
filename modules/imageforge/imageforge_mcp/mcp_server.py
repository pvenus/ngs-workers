from __future__ import annotations

import json
import io
import re
import statistics
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from PIL import Image, UnidentifiedImageError

from . import storage
from .image_ops import (
    Component,
    find_components,
    remove_background_from_corners,
    select_connected_layer,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOWNLOADS_ROOT = storage.downloads_root()
GENERATED_IMAGE_ROOT = storage.generated_root()
SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}

mcp = MCPServer(
    name="imageforge",
    title="ImageForge MCP",
    version="1.2.0",
    instructions=(
        "ImageForge edits local raster images for Codex. Pass an absolute image_path. "
        "Use separate_sprite_frames for background removal and frame separation, then "
        "transform_frame_files for independent position and scale changes. Use magic_select_object "
        "for a clicked object. Frame packages use the shared imageforge jobs namespace."
    ),
)


def _load_image(image_path: str) -> tuple[Path, Image.Image]:
    path = Path(image_path).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"Image file not found: {path}")
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported image extension: {path.suffix}")
    try:
        if (path.is_relative_to(storage.service_root()) or
                path.is_relative_to(storage.LEGACY_DOWNLOADS) or
                path.is_relative_to(storage.LEGACY_GENERATED)):
            image = Image.open(io.BytesIO(storage.read_verified_bytes(path))).convert("RGBA")
        else:
            image = Image.open(path).convert("RGBA")
        image.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError(f"Invalid or unreadable image: {path}") from exc
    return path, image


def _new_output_directory(source: Path, operation: str) -> Path:
    return storage.new_job(uuid.uuid4().hex, "intermediate")


def _spatial_order(components: list[Component]) -> list[Component]:
    if not components:
        return []
    row_tolerance = max(8.0, statistics.median(item.height for item in components) * 0.55)
    remaining = sorted(components, key=lambda item: item.y + item.height / 2)
    ordered: list[Component] = []
    while remaining:
        anchor = remaining[0].y + remaining[0].height / 2
        row = [item for item in remaining if abs((item.y + item.height / 2) - anchor) <= row_tolerance]
        ordered.extend(sorted(row, key=lambda item: item.x))
        row_ids = {id(item) for item in row}
        remaining = [item for item in remaining if id(item) not in row_ids]
    return ordered


def _component_rows(components: list[Component]) -> list[list[Component]]:
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


def _safe_name(value: str, fallback: str) -> str:
    return re.sub(r"[^\w.-]+", "_", value, flags=re.UNICODE).strip("._")[:80] or fallback


def _package_directory(asset_name: str, action_name: str) -> Path:
    job_id = uuid.uuid4().hex
    asset = f"{_safe_name(asset_name, 'image')}_{job_id[:8]}"
    adopted = storage.new_job(job_id, "adopted")
    asset_dir = storage.new_directory(adopted, asset)
    return storage.new_directory(asset_dir, _safe_name(action_name, "Frames"))


def _grid_frames(
    source_width: int,
    source_height: int,
    rows: list[list[Component]],
    layers: list[Image.Image],
    horizontal_padding: int = 20,
) -> tuple[list[Image.Image], tuple[int, int]]:
    row_count = len(rows)
    frame_width = max(layer.width for layer in layers) + horizontal_padding * 2
    frame_height = (source_height + row_count - 1) // row_count
    frames: list[Image.Image] = []
    layer_index = 0
    for row_index, row in enumerate(rows):
        for component in row:
            frame = Image.new("RGBA", (frame_width, frame_height), (255, 255, 255, 0))
            frame.alpha_composite(
                layers[layer_index],
                ((frame_width - layers[layer_index].width) // 2, component.y - row_index * frame_height),
            )
            frames.append(frame)
            layer_index += 1
    return frames, (frame_width, frame_height)


def _fixed_frames(
    layers: list[Image.Image], frame_height: int, horizontal_margin: int, bottom_padding: int
) -> list[Image.Image]:
    if not layers:
        return []
    if any(value < 0 for value in (horizontal_margin, bottom_padding)) or frame_height < 16:
        raise ValueError("frame_height must be at least 16; margins cannot be negative")
    frame_width = max(layer.width for layer in layers) + horizontal_margin * 2
    frames: list[Image.Image] = []
    for layer in layers:
        frame = Image.new("RGBA", (frame_width, frame_height), (255, 255, 255, 0))
        x = (frame_width - layer.width) // 2
        y = frame_height - bottom_padding - layer.height
        frame.alpha_composite(layer, (x, y))
        frames.append(frame)
    return frames


@mcp.tool(structured_output=True)
def inspect_image(image_path: str) -> dict[str, Any]:
    """Inspect a local image before editing it. This tool does not modify or save files."""
    path, image = _load_image(image_path)
    alpha = image.getchannel("A")
    extrema = alpha.getextrema()
    return {
        "path": str(path),
        "width": image.width,
        "height": image.height,
        "mode": image.mode,
        "has_transparency": extrema[0] < 255,
    }


@mcp.tool(structured_output=True)
def remove_image_background(
    image_path: str,
    tolerance: float = 5,
    feather: float = 1,
    sample_radius: int = 2,
) -> dict[str, Any]:
    """Remove background colors connected to the four image corners and save a transparent PNG."""
    if not 0 <= tolerance <= 100:
        raise ValueError("tolerance must be between 0 and 100")
    if not 0 <= feather <= 25:
        raise ValueError("feather must be between 0 and 25")
    path, image = _load_image(image_path)
    result = remove_background_from_corners(image, tolerance, feather, sample_radius)
    directory = _new_output_directory(path, "background")
    output = directory / "background_removed.png"
    storage.save_png(result, output)
    storage.commit(directory, origin="intermediate", source=str(path))
    return {"ok": True, "output_path": str(output), "width": result.width, "height": result.height}


@mcp.tool(structured_output=True)
def magic_select_object(
    image_path: str,
    x: int,
    y: int,
    alpha_threshold: int = 120,
    remove_corner_background: bool = False,
    tolerance: float = 5,
) -> dict[str, Any]:
    """Select the connected opaque object at (x, y), copy its exact alpha pixels, and save it as a PNG layer."""
    path, image = _load_image(image_path)
    if remove_corner_background:
        image = remove_background_from_corners(image, tolerance, feather=1, sample_radius=2)
    layer, component = select_connected_layer(image, (x, y), alpha_threshold)
    directory = _new_output_directory(path, "selection")
    output = directory / "selected_object.png"
    storage.save_png(layer, output)
    storage.commit(directory, origin="intermediate", source=str(path))
    return {"ok": True, "output_path": str(output), "component": asdict(component)}


@mcp.tool(structured_output=True)
def separate_sprite_frames(
    image_path: str,
    expected_object_count: int,
    asset_name: str = "",
    action_name: str = "Frames",
    min_pixels: int = 2000,
    horizontal_padding: int = 20,
) -> dict[str, Any]:
    """Only remove the background and separate objects into a generated_image asset/action package."""
    if expected_object_count < 1 or min_pixels < 1 or horizontal_padding < 0:
        raise ValueError("counts must be positive and horizontal_padding cannot be negative")
    path, source = _load_image(image_path)
    attempts: list[dict[str, int]] = []
    for tolerance in (4, 3, 2, 1):
        transparent = remove_background_from_corners(source, tolerance, feather=1, sample_radius=2)
        for threshold in (150, 140, 130, 120, 110, 100):
            rows = _component_rows(find_components(transparent, threshold, min_pixels))
            components = [item for row in rows for item in row]
            attempts.append({"background_tolerance": tolerance, "alpha_threshold": threshold, "count": len(components)})
            if len(components) != expected_object_count:
                continue
            layers = [select_connected_layer(transparent, (item.seed_x, item.seed_y), threshold)[0] for item in components]
            frames, canvas = _grid_frames(source.width, source.height, rows, layers, horizontal_padding)
            directory = _package_directory(asset_name or path.stem, action_name)
            storage.save_png(source, directory / "imagegen-source.png")
            storage.save_png(transparent, directory / "background-removed.png")
            frame_paths: list[str] = []
            for index, frame in enumerate(frames):
                output = directory / f"frame-{index}.png"
                storage.save_png(frame, output)
                frame_paths.append(str(output))
            manifest = {"asset": _safe_name(asset_name or path.stem, "image"), "action": _safe_name(action_name, "Frames"), "canvas": list(canvas), "frameCount": len(frames), "rowCount": len(rows), "horizontalPadding": horizontal_padding, "selectedSettings": {"backgroundTolerance": tolerance, "alphaThreshold": threshold}}
            manifest_path = directory / "frame-manifest.json"
            storage.save_json(manifest_path, manifest)
            storage.commit(directory, origin="adopted", source=str(path), settings=manifest)
            return {"ok": True, "output_directory": str(directory), "frames": frame_paths, "manifest_path": str(manifest_path), "attempts": attempts, **manifest}
    raise ValueError(f"Expected {expected_object_count} objects were not found. Attempts: {attempts}")


@mcp.tool(structured_output=True)
def transform_frame_files(
    frame_paths: list[str],
    canvas_width: int,
    canvas_height: int,
    offsets_x: list[int] | None = None,
    offsets_y: list[int] | None = None,
    scales: list[float] | None = None,
    asset_name: str = "frames",
    action_name: str = "Frames",
    overwrite_originals: bool = False,
) -> dict[str, Any]:
    """Move and scale listed frames; overwrite_originals creates a new adopted version."""
    if not frame_paths or canvas_width < 1 or canvas_height < 1:
        raise ValueError("frame_paths and positive canvas dimensions are required")
    offsets_x = offsets_x or []
    offsets_y = offsets_y or []
    scales = scales or []
    transformed_images: list[Image.Image] = []
    source_paths: list[Path] = []
    transforms: list[dict[str, float | int]] = []
    for index, frame_path in enumerate(frame_paths):
        source_path, frame = _load_image(frame_path)
        x = offsets_x[index] if index < len(offsets_x) else 0
        y = offsets_y[index] if index < len(offsets_y) else 0
        scale = scales[index] if index < len(scales) else 1.0
        if not 0.05 <= scale <= 8.0:
            raise ValueError(f"Frame {index} scale must be 0.05..8.0")
        resized = frame.resize((max(1, round(frame.width * scale)), max(1, round(frame.height * scale))), Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", (canvas_width, canvas_height), (255, 255, 255, 0))
        canvas.alpha_composite(resized, ((canvas_width - resized.width) // 2 + x, (canvas_height - resized.height) // 2 + y))
        transformed_images.append(canvas)
        source_paths.append(source_path)
        transforms.append({"x": x, "y": y, "scale": scale})
    if overwrite_originals:
        root = GENERATED_IMAGE_ROOT.resolve()
        legacy_root = storage.LEGACY_GENERATED.resolve()
        if any((not path.is_relative_to(root) and not path.is_relative_to(legacy_root))
               or not re.fullmatch(r"frame[-_]\d+\.png", path.name)
               or not storage.verified_file(path) for path in source_paths):
            raise ValueError("overwrite_originals requires verified generated frames")
        if len({path.parent for path in source_paths}) != 1:
            raise ValueError("all source frames must belong to one action folder")
    directory = _package_directory(asset_name, action_name)
    outputs = []
    for index, image in enumerate(transformed_images):
        output = directory / f"frame-{index}.png"
        storage.save_png(image, output)
        outputs.append(str(output))
    manifest = {"asset": _safe_name(asset_name, "frames"), "action": _safe_name(action_name, "Frames"), "canvas": [canvas_width, canvas_height], "frameCount": len(outputs), "appliedTransforms": transforms, "transforms": [{"x": 0, "y": 0, "scale": 1.0} for _ in outputs]}
    manifest_path = directory / "transform-manifest.json"
    storage.save_json(manifest_path, manifest)
    storage.commit(directory, origin="adopted", source=",".join(str(p) for p in source_paths),
                   settings=manifest)
    return {"ok": True, "output_directory": str(directory), "frames": outputs, "manifest_path": str(manifest_path), **manifest}


@mcp.tool(structured_output=True)
def extract_sprite_frames(
    image_path: str,
    tolerance: float = 5,
    alpha_threshold: int = 120,
    min_pixels: int = 2000,
    frame_height: int = 340,
    horizontal_margin: int = 120,
    bottom_padding: int = 20,
) -> dict[str, Any]:
    """Remove the corner background, find alpha-connected objects, and export spatially ordered fixed-size PNG frames."""
    if not 0 <= tolerance <= 100:
        raise ValueError("tolerance must be between 0 and 100")
    if not 1 <= alpha_threshold <= 255 or min_pixels < 1:
        raise ValueError("alpha_threshold must be 1..255 and min_pixels must be positive")
    path, source = _load_image(image_path)
    transparent = remove_background_from_corners(source, tolerance, feather=1, sample_radius=2)
    components = _spatial_order(find_components(transparent, alpha_threshold, min_pixels))
    layers = [
        select_connected_layer(transparent, (item.seed_x, item.seed_y), alpha_threshold)[0]
        for item in components
    ]
    frames = _fixed_frames(layers, frame_height, horizontal_margin, bottom_padding)
    directory = _new_output_directory(path, "frames")
    background_path = directory / "background_removed.png"
    storage.save_png(transparent, background_path)
    frame_paths: list[str] = []
    for index, frame in enumerate(frames, 1):
        output = directory / f"frame_{index:03d}.png"
        storage.save_png(frame, output)
        frame_paths.append(str(output))
    manifest = {
        "source_path": str(path),
        "background_removed_path": str(background_path),
        "count": len(frames),
        "frame_width": frames[0].width if frames else 0,
        "frame_height": frame_height,
        "frames": frame_paths,
        "components": [asdict(component) for component in components],
        "settings": {
            "tolerance": tolerance,
            "alpha_threshold": alpha_threshold,
            "min_pixels": min_pixels,
            "horizontal_margin": horizontal_margin,
            "bottom_padding": bottom_padding,
        },
    }
    manifest_path = directory / "manifest.json"
    storage.save_json(manifest_path, manifest)
    storage.commit(directory, origin="intermediate", source=str(path), settings=manifest)
    return {"ok": True, "output_directory": str(directory), "manifest_path": str(manifest_path), **manifest}


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
