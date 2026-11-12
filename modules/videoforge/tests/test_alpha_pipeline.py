import tempfile
import unittest
from pathlib import Path

import imageio.v3 as iio
import numpy as np

from videoforge_mcp.alpha_core import build_trimap, fuse_mattes, luminance_alpha, rgba
from videoforge_mcp.alpha_pipeline import AlphaConfig, run_alpha_pipeline


class FakeTracker:
    def track(self, frame_dir, initial_mask):
        for index, _ in enumerate(sorted(frame_dir.glob("*.png"))):
            yield index, initial_mask


class FakeMatting:
    def predict(self, rgb):
        result = np.zeros(rgb.shape[:2], dtype=np.float32)
        result[2:6, 2:6] = 0.5
        result[3:5, 3:5] = 1
        return result


class AlphaCoreTests(unittest.TestCase):
    def test_luminance_and_rgba(self):
        rgb = np.zeros((2, 2, 3), dtype=np.uint8)
        rgb[0, 0] = [255, 0, 0]
        alpha = luminance_alpha(rgb, threshold=0)
        self.assertEqual(alpha[0, 0], 1)
        self.assertEqual(alpha[1, 1], 0)
        self.assertEqual(rgba(rgb, alpha).shape, (2, 2, 4))

    def test_trimap_fusion_preserves_certain_regions(self):
        coarse = np.zeros((9, 9), dtype=np.float32)
        coarse[2:7, 2:7] = 1
        refined = np.full((9, 9), 0.4, dtype=np.float32)
        foreground, uncertain, background = build_trimap(coarse, radius=1)
        fused = fuse_mattes(coarse, refined, radius=1)
        self.assertTrue(np.all(fused[foreground] == 1))
        self.assertTrue(np.all(fused[background] == 0))
        self.assertTrue(np.allclose(fused[uncertain], 0.4))


class AlphaPipelineTests(unittest.TestCase):
    def _video(self, root):
        path = root / "input.mp4"
        frames = []
        for index in range(3):
            frame = np.zeros((8, 8, 3), dtype=np.uint8)
            frame[2:6, 2:6] = 100 + index * 20
            frames.append(frame)
        iio.imwrite(path, frames, fps=3, codec="libx264", macro_block_size=1)
        return path

    def test_subject_pipeline_is_atomic_and_streamed_to_rgba(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mask = np.zeros((8, 8), dtype=np.float32)
            mask[2:6, 2:6] = 1
            output = root / "alpha"
            result = run_alpha_pipeline(self._video(root), output, AlphaConfig(), initial_mask=mask, tracker=FakeTracker(), matting=FakeMatting())
            self.assertEqual(result["frame_count"], 3)
            self.assertEqual(len(list((output / "rgba").glob("*.png"))), 3)
            self.assertEqual(iio.imread(output / "rgba/000000.png").shape[2], 4)
            self.assertTrue((output / "manifest.json").is_file())

    def test_effect_mode_needs_no_models(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "effect"
            run_alpha_pipeline(self._video(root), output, AlphaConfig(mode="effect-luma", temporal_strength=0))
            alpha = iio.imread(output / "alpha_stabilized/000000.png")
            self.assertEqual(int(alpha[0, 0]), 0)
            self.assertGreater(int(alpha[3, 3]), 0)


if __name__ == "__main__":
    unittest.main()
