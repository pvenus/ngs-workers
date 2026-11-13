from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageFilter


@dataclass(frozen=True)
class Component:
    x: int
    y: int
    width: int
    height: int
    pixels: int
    seed_x: int = -1
    seed_y: int = -1

    @property
    def box(self) -> tuple[int, int, int, int]:
        return self.x, self.y, self.x + self.width, self.y + self.height


def _color_distance(rgb: np.ndarray, target: np.ndarray) -> np.ndarray:
    """Perceptually weighted RGB distance, normalized to roughly 0..100."""
    delta = rgb.astype(np.float32) - target.astype(np.float32)
    distance = np.sqrt(
        delta[..., 0] ** 2 * 0.299
        + delta[..., 1] ** 2 * 0.587
        + delta[..., 2] ** 2 * 0.114
    )
    return distance * (100.0 / 255.0)


def remove_background(
    image: Image.Image,
    seed: tuple[int, int],
    tolerance: float,
    contiguous: bool = True,
    feather: float = 0,
) -> Image.Image:
    rgba = np.array(image.convert("RGBA"), dtype=np.uint8)
    height, width = rgba.shape[:2]
    sx, sy = seed
    if not (0 <= sx < width and 0 <= sy < height):
        raise ValueError("Seed point is outside the image")

    target = rgba[sy, sx, :3]
    candidates = _color_distance(rgba[..., :3], target) <= float(tolerance)

    if contiguous:
        selected = np.zeros((height, width), dtype=bool)
        queue: deque[tuple[int, int]] = deque([(sx, sy)])
        selected[sy, sx] = bool(candidates[sy, sx])
        while queue:
            x, y = queue.popleft()
            for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if (
                    0 <= nx < width
                    and 0 <= ny < height
                    and candidates[ny, nx]
                    and not selected[ny, nx]
                ):
                    selected[ny, nx] = True
                    queue.append((nx, ny))
    else:
        selected = candidates

    if feather > 0:
        mask = Image.fromarray((selected * 255).astype(np.uint8), "L")
        mask = mask.filter(ImageFilter.GaussianBlur(radius=float(feather)))
        removal = np.asarray(mask, dtype=np.uint8)
    else:
        removal = (selected * 255).astype(np.uint8)

    old_alpha = rgba[..., 3].astype(np.uint16)
    rgba[..., 3] = (old_alpha * (255 - removal).astype(np.uint16) // 255).astype(np.uint8)
    return Image.fromarray(rgba)


def remove_background_from_corners(
    image: Image.Image,
    tolerance: float,
    feather: float = 0,
    sample_radius: int = 2,
) -> Image.Image:
    """Remove background connected to four corners using their median colors."""
    rgba = np.array(image.convert("RGBA"), dtype=np.uint8)
    height, width = rgba.shape[:2]
    radius = max(0, int(sample_radius))
    corner_points = ((0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1))
    targets: list[np.ndarray] = []
    for x, y in corner_points:
        x1, x2 = max(0, x - radius), min(width, x + radius + 1)
        y1, y2 = max(0, y - radius), min(height, y + radius + 1)
        patch = rgba[y1:y2, x1:x2, :3].reshape(-1, 3)
        targets.append(np.median(patch, axis=0).astype(np.uint8))

    candidates = np.zeros((height, width), dtype=bool)
    for target in targets:
        candidates |= _color_distance(rgba[..., :3], target) <= float(tolerance)

    selected = np.zeros((height, width), dtype=bool)
    queue: deque[tuple[int, int]] = deque()
    for x, y in corner_points:
        if candidates[y, x] and not selected[y, x]:
            selected[y, x] = True
            queue.append((x, y))
    while queue:
        x, y = queue.popleft()
        for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if 0 <= nx < width and 0 <= ny < height and candidates[ny, nx] and not selected[ny, nx]:
                selected[ny, nx] = True
                queue.append((nx, ny))

    if feather > 0:
        mask = Image.fromarray((selected * 255).astype(np.uint8), "L")
        removal = np.asarray(mask.filter(ImageFilter.GaussianBlur(radius=float(feather))), dtype=np.uint8)
    else:
        removal = (selected * 255).astype(np.uint8)
    old_alpha = rgba[..., 3].astype(np.uint16)
    rgba[..., 3] = (old_alpha * (255 - removal).astype(np.uint16) // 255).astype(np.uint8)
    return Image.fromarray(rgba)


def find_components(image: Image.Image, alpha_threshold: int = 1, min_pixels: int = 10) -> list[Component]:
    alpha = np.asarray(image.convert("RGBA"))[..., 3]
    foreground = alpha >= alpha_threshold
    height, width = foreground.shape
    visited = np.zeros_like(foreground, dtype=bool)
    components: list[Component] = []

    for y in range(height):
        for x in range(width):
            if not foreground[y, x] or visited[y, x]:
                continue
            queue: deque[tuple[int, int]] = deque([(x, y)])
            visited[y, x] = True
            min_x = max_x = x
            min_y = max_y = y
            count = 0
            while queue:
                cx, cy = queue.popleft()
                count += 1
                min_x, max_x = min(min_x, cx), max(max_x, cx)
                min_y, max_y = min(min_y, cy), max(max_y, cy)
                for nx, ny in ((cx - 1, cy), (cx + 1, cy), (cx, cy - 1), (cx, cy + 1)):
                    if 0 <= nx < width and 0 <= ny < height and foreground[ny, nx] and not visited[ny, nx]:
                        visited[ny, nx] = True
                        queue.append((nx, ny))
            if count >= min_pixels:
                components.append(Component(min_x, min_y, max_x - min_x + 1, max_y - min_y + 1, count, x, y))

    return sorted(components, key=lambda item: item.pixels, reverse=True)


def crop_component(image: Image.Image, component: Component, padding: int = 0) -> Image.Image:
    x1 = max(0, component.x - padding)
    y1 = max(0, component.y - padding)
    x2 = min(image.width, component.x + component.width + padding)
    y2 = min(image.height, component.y + component.height + padding)
    return image.crop((x1, y1, x2, y2))


def select_connected_layer(
    image: Image.Image,
    seed: tuple[int, int],
    alpha_threshold: int = 1,
) -> tuple[Image.Image, Component]:
    """Copy the connected opaque pixels at seed into a transparent cropped layer."""
    rgba = np.asarray(image.convert("RGBA"))
    height, width = rgba.shape[:2]
    sx, sy = seed
    if not (0 <= sx < width and 0 <= sy < height):
        raise ValueError("Seed point is outside the image")
    foreground = rgba[..., 3] >= alpha_threshold
    if not foreground[sy, sx]:
        raise ValueError("Selected point is transparent")

    selected = np.zeros((height, width), dtype=bool)
    selected[sy, sx] = True
    queue: deque[tuple[int, int]] = deque([(sx, sy)])
    min_x = max_x = sx
    min_y = max_y = sy
    count = 0
    while queue:
        x, y = queue.popleft()
        count += 1
        min_x, max_x = min(min_x, x), max(max_x, x)
        min_y, max_y = min(min_y, y), max(max_y, y)
        for nx, ny in (
            (x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1),
            (x - 1, y - 1), (x + 1, y - 1), (x - 1, y + 1), (x + 1, y + 1),
        ):
            if 0 <= nx < width and 0 <= ny < height and foreground[ny, nx] and not selected[ny, nx]:
                selected[ny, nx] = True
                queue.append((nx, ny))

    component = Component(min_x, min_y, max_x - min_x + 1, max_y - min_y + 1, count, sx, sy)
    crop = rgba[min_y:max_y + 1, min_x:max_x + 1].copy()
    crop_selected = selected[min_y:max_y + 1, min_x:max_x + 1]
    crop[~crop_selected] = 0
    return Image.fromarray(crop), component
