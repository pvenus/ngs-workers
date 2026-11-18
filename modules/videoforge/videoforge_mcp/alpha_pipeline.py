"""Disk-streamed video-to-RGBA pipeline designed for 16 GB development Macs."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import json
import os
import shutil
import tempfile
from typing import Any, Protocol

import imageio.v3 as iio
import numpy as np

from .alpha_core import fuse_mattes, luminance_alpha, rgba, stabilize_alpha, warp_previous_alpha


class MatteModel(Protocol):
    def predict(self, rgb: np.ndarray) -> np.ndarray: ...


@dataclass(frozen=True)
class AlphaConfig:
    mode: str = "subject"
    trimap_radius: int = 6
    temporal_strength: float = 0.25
    luminance_threshold: float = 0.04
    luminance_gamma: float = 1.0
    max_frames: int = 1200

    def validate(self) -> None:
        if self.mode not in {"subject", "effect-luma"}:
            raise ValueError("mode must be subject or effect-luma")
        if not 0 <= self.temporal_strength <= 0.8:
            raise ValueError("temporal_strength must be between 0 and 0.8")
        if not 0 <= self.luminance_threshold < 1 or self.luminance_gamma <= 0:
            raise ValueError("invalid luminance settings")
        if not 1 <= self.max_frames <= 12000:
            raise ValueError("max_frames must be between 1 and 12000")


def _write_png(path: Path, value: np.ndarray) -> None:
    iio.imwrite(path, value)


def _materialize_frames(source: Path, frame_dir: Path, max_frames: int) -> tuple[int, float]:
    meta = iio.immeta(source)
    fps = float(meta.get("fps") or 0)
    count = 0
    for count, frame in enumerate(iio.imiter(source), start=1):
        if count > max_frames:
            raise ValueError("frame_limit_exceeded")
        _write_png(frame_dir / f"{count - 1:06d}.png", np.asarray(frame, dtype=np.uint8)[..., :3])
    if count == 0:
        raise ValueError("video_has_no_frames")
    return count, fps


def run_alpha_pipeline(
    source: Path,
    output_dir: Path,
    config: AlphaConfig,
    *,
    initial_mask: np.ndarray | None = None,
    tracker: Any | None = None,
    matting: MatteModel | None = None,
) -> dict[str, Any]:
    config.validate()
    source = source.resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if output_dir.exists():
        raise FileExistsError("alpha_output_exists")
    if config.mode == "subject" and (initial_mask is None or tracker is None or matting is None):
        raise ValueError("subject mode requires initial_mask, tracker and matting")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.alpha-", dir=output_dir.parent))
    try:
        dirs = {name: stage / name for name in ("frames_rgb", "masks_coarse", "alpha_raw", "alpha_stabilized", "rgba")}
        for path in dirs.values():
            path.mkdir()
        frame_count, fps = _materialize_frames(source, dirs["frames_rgb"], config.max_frames)
        coarse_by_index: dict[int, np.ndarray] = {}
        if config.mode == "subject":
            coarse_by_index = {index: mask for index, mask in tracker.track(dirs["frames_rgb"], initial_mask)}
            if len(coarse_by_index) != frame_count:
                raise RuntimeError("tracker_frame_count_mismatch")
        previous_rgb = previous_alpha = None
        diagnostics: list[dict[str, Any]] = []
        for index in range(frame_count):
            rgb_frame = iio.imread(dirs["frames_rgb"] / f"{index:06d}.png")
            if config.mode == "effect-luma":
                coarse = raw = luminance_alpha(rgb_frame, config.luminance_threshold, config.luminance_gamma)
            else:
                coarse = coarse_by_index[index]
                raw = fuse_mattes(coarse, matting.predict(rgb_frame), config.trimap_radius)
            warped = None if previous_alpha is None else warp_previous_alpha(previous_alpha, previous_rgb, rgb_frame)
            stable = stabilize_alpha(raw, warped, config.temporal_strength)
            if previous_alpha is not None:
                delta = float(np.mean(np.abs(stable - warped)))
                if delta > 0.2:
                    diagnostics.append({"frame": index, "code": "large_alpha_change", "mean_delta": round(delta, 4)})
            name = f"{index:06d}.png"
            _write_png(dirs["masks_coarse"] / name, np.round(coarse * 255).astype(np.uint8))
            _write_png(dirs["alpha_raw"] / name, np.round(raw * 255).astype(np.uint8))
            _write_png(dirs["alpha_stabilized"] / name, np.round(stable * 255).astype(np.uint8))
            _write_png(dirs["rgba"] / name, rgba(rgb_frame, stable))
            previous_rgb, previous_alpha = rgb_frame, stable
        manifest = {
            "schema_version": 1,
            "source": str(source),
            "frame_count": frame_count,
            "fps": fps or None,
            "config": asdict(config),
            "directories": {key: str(output_dir / key) for key in dirs},
            "diagnostics": diagnostics,
        }
        (stage / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        os.rename(stage, output_dir)
        return manifest
    finally:
        if stage.exists():
            shutil.rmtree(stage)
