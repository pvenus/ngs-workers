"""Small, model-independent alpha compositing primitives."""
from __future__ import annotations

import numpy as np


def normalize_alpha(value: np.ndarray) -> np.ndarray:
    array = np.asarray(value, dtype=np.float32)
    if array.ndim == 3:
        array = array[..., 0]
    if array.size and float(array.max()) > 1.0:
        array /= 255.0
    return np.clip(array, 0.0, 1.0)


def luminance_alpha(rgb: np.ndarray, threshold: float = 0.04, gamma: float = 1.0) -> np.ndarray:
    """Build an alpha matte for bright VFX rendered over black."""
    image = np.asarray(rgb, dtype=np.float32)
    if image.max(initial=0) > 1.0:
        image /= 255.0
    luminance = image[..., :3].max(axis=2)
    matte = np.clip((luminance - threshold) / max(1e-6, 1.0 - threshold), 0.0, 1.0)
    return np.power(matte, max(gamma, 1e-6), dtype=np.float32)


def build_trimap(mask: np.ndarray, radius: int = 6) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return definite foreground, uncertain border and definite background masks."""
    alpha = normalize_alpha(mask)
    solid = alpha >= 0.5
    if radius <= 0:
        return solid, np.zeros_like(solid), ~solid
    try:
        import cv2
        kernel = np.ones((radius * 2 + 1, radius * 2 + 1), np.uint8)
        eroded = cv2.erode(solid.astype(np.uint8), kernel).astype(bool)
        dilated = cv2.dilate(solid.astype(np.uint8), kernel).astype(bool)
    except ImportError:
        # Tests and minimal installs still work; production installs use OpenCV.
        eroded = solid.copy()
        dilated = solid.copy()
        for _ in range(radius):
            padded = np.pad(eroded, 1, constant_values=True)
            eroded = np.logical_and.reduce([padded[1:-1, 1:-1], padded[:-2, 1:-1], padded[2:, 1:-1], padded[1:-1, :-2], padded[1:-1, 2:]])
            padded = np.pad(dilated, 1, constant_values=False)
            dilated = np.logical_or.reduce([padded[1:-1, 1:-1], padded[:-2, 1:-1], padded[2:, 1:-1], padded[1:-1, :-2], padded[1:-1, 2:]])
    uncertain = dilated & ~eroded
    return eroded, uncertain, ~dilated


def fuse_mattes(coarse: np.ndarray, refined: np.ndarray, radius: int = 6) -> np.ndarray:
    foreground, uncertain, background = build_trimap(coarse, radius)
    result = normalize_alpha(refined)
    result[foreground] = 1.0
    result[background] = 0.0
    # Keep this assignment explicit: only the uncertain band comes from the matting model.
    result[uncertain] = normalize_alpha(refined)[uncertain]
    return result


def stabilize_alpha(current: np.ndarray, previous_warped: np.ndarray | None, strength: float = 0.25) -> np.ndarray:
    current = normalize_alpha(current)
    if previous_warped is None or strength <= 0:
        return current
    previous = normalize_alpha(previous_warped)
    if previous.shape != current.shape:
        raise ValueError("alpha_shape_changed")
    return np.clip(current * (1.0 - strength) + previous * strength, 0.0, 1.0)


def warp_previous_alpha(previous: np.ndarray, previous_rgb: np.ndarray, current_rgb: np.ndarray) -> np.ndarray:
    """Warp the previous matte to the current frame with Farneback optical flow."""
    try:
        import cv2
    except ImportError:
        return normalize_alpha(previous)
    prev_gray = cv2.cvtColor(np.asarray(previous_rgb)[..., :3], cv2.COLOR_RGB2GRAY)
    curr_gray = cv2.cvtColor(np.asarray(current_rgb)[..., :3], cv2.COLOR_RGB2GRAY)
    flow = cv2.calcOpticalFlowFarneback(prev_gray, curr_gray, None, 0.5, 3, 15, 3, 5, 1.2, 0)
    height, width = prev_gray.shape
    grid_x, grid_y = np.meshgrid(np.arange(width), np.arange(height))
    map_x = (grid_x - flow[..., 0]).astype(np.float32)
    map_y = (grid_y - flow[..., 1]).astype(np.float32)
    return cv2.remap(normalize_alpha(previous), map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)


def rgba(rgb: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    color = np.asarray(rgb, dtype=np.uint8)[..., :3]
    channel = np.round(normalize_alpha(alpha) * 255.0).astype(np.uint8)
    return np.dstack((color, channel))
