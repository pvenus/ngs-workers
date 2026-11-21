"""Lazy model adapters so tests and lightweight development need no model downloads."""
from __future__ import annotations

from pathlib import Path
import os
import shutil
import tempfile
from typing import Iterator

import numpy as np

from .alpha_core import normalize_alpha


class BiRefNetMatting:
    def __init__(self, model_id: str = "ZhengPeng7/BiRefNet-matting", device: str | None = None):
        self.model_id = model_id
        self.device_name = device
        self._model = None
        self._device = None

    def _load(self) -> None:
        if self._model is not None:
            return
        import torch
        from transformers import AutoModelForImageSegmentation
        if self.device_name:
            device = torch.device(self.device_name)
        elif torch.backends.mps.is_available():
            device = torch.device("mps")
        elif torch.cuda.is_available():
            device = torch.device("cuda")
        else:
            device = torch.device("cpu")
        self._model = AutoModelForImageSegmentation.from_pretrained(self.model_id, trust_remote_code=True).eval().to(device)
        self._device = device

    def predict(self, rgb: np.ndarray) -> np.ndarray:
        self._load()
        import torch
        from torchvision.transforms import Compose, Normalize, Resize, ToTensor
        from PIL import Image
        height, width = rgb.shape[:2]
        inference_size = int(os.environ.get("VIDEOFORGE_MATTING_SIZE", "1024"))
        if inference_size < 256 or inference_size % 32:
            raise ValueError("VIDEOFORGE_MATTING_SIZE must be >= 256 and divisible by 32")
        transform = Compose([Resize((inference_size, inference_size)), ToTensor(), Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])])
        tensor = transform(Image.fromarray(np.asarray(rgb, dtype=np.uint8)[..., :3])).unsqueeze(0).to(self._device)
        with torch.inference_mode():
            prediction = self._model(tensor)[-1].sigmoid().cpu()[0, 0].numpy()
        return np.asarray(Image.fromarray(prediction).resize((width, height), Image.Resampling.BILINEAR), dtype=np.float32)


class SAM2VideoTracker:
    def __init__(self, model_id: str = "facebook/sam2.1-hiera-small", device: str | None = None):
        self.model_id = model_id
        self.device = device

    def track(self, frame_dir: Path, initial_mask: np.ndarray) -> Iterator[tuple[int, np.ndarray]]:
        import torch
        from PIL import Image
        from sam2.sam2_video_predictor import SAM2VideoPredictor
        predictor = SAM2VideoPredictor.from_pretrained(self.model_id, device=self.device)
        # SAM2's directory loader accepts JPEG filenames only. Keep the pipeline's
        # lossless PNG frames and materialize a short-lived JPEG view for tracking.
        jpeg_dir = Path(tempfile.mkdtemp(prefix="sam2-frames-", dir=frame_dir.parent))
        try:
            for source in sorted(frame_dir.glob("*.png")):
                with Image.open(source) as image:
                    image.convert("RGB").save(jpeg_dir / f"{source.stem}.jpg", quality=95)
            state = predictor.init_state(video_path=str(jpeg_dir), offload_video_to_cpu=True, offload_state_to_cpu=True)
            predictor.add_new_mask(state, frame_idx=0, obj_id=1, mask=normalize_alpha(initial_mask) >= 0.5)
            with torch.inference_mode():
                for index, _, logits in predictor.propagate_in_video(state):
                    yield int(index), (logits[0, 0].cpu().numpy() > 0).astype(np.float32)
        finally:
            shutil.rmtree(jpeg_dir, ignore_errors=True)
