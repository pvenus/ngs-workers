import tempfile
import os
import hashlib
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

from imageforge_mcp import mcp_server, storage


class McpServerToolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.mount = self.root / "volume"
        self.mount.mkdir()
        self.root_env = self.mount / "MCP/resources/v1"
        self.patches = [
            patch.object(storage, "MOUNT", self.mount),
            patch.dict(os.environ, {"IMAGEFORGE_RESOURCE_ROOT": str(self.root_env)}),
            patch.object(storage, "guard_volume"),
            patch.object(mcp_server, "DOWNLOADS_ROOT", self.root_env / "imageforge/jobs"),
            patch.object(mcp_server, "GENERATED_IMAGE_ROOT", self.root_env / "imageforge/jobs"),
            patch.object(storage, "PROJECT_ROOT", self.root),
            patch.object(storage, "LEGACY_GENERATED", self.root / "generated_image"),
            patch.object(storage, "LEGACY_INVENTORY", {}),
        ]
        for item in self.patches:
            item.start()
        self.source = self.root / "sheet.png"
        image = Image.new("RGBA", (30, 20), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((2, 3, 8, 16), fill="red")
        draw.rectangle((20, 3, 26, 16), fill="blue")
        image.save(self.source)

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def test_inspect_and_background_removal(self):
        info = mcp_server.inspect_image(str(self.source))
        self.assertEqual((info["width"], info["height"]), (30, 20))
        with patch.object(mcp_server, "DOWNLOADS_ROOT", storage.downloads_root()):
            result = mcp_server.remove_image_background(str(self.source), tolerance=0, feather=0)
        self.assertTrue(Path(result["output_path"]).is_file())
        self.assertEqual(Image.open(result["output_path"]).getpixel((0, 0))[3], 0)

    def test_extracts_fixed_size_frames(self):
        with patch.object(mcp_server, "DOWNLOADS_ROOT", storage.downloads_root()):
            result = mcp_server.extract_sprite_frames(
                str(self.source), tolerance=0, min_pixels=5, frame_height=24, horizontal_margin=3
            )
        self.assertEqual(result["count"], 2)
        sizes = set()
        for path in result["frames"]:
            with Image.open(path) as frame:
                sizes.add(frame.size)
        self.assertEqual(sizes, {(13, 24)})
        self.assertTrue(Path(result["manifest_path"]).is_file())

    def test_separate_and_transform_are_independent_tools(self):
        generated = storage.generated_root()
        with patch.object(mcp_server, "GENERATED_IMAGE_ROOT", generated):
            separated = mcp_server.separate_sprite_frames(
                str(self.source), expected_object_count=2, min_pixels=5,
                asset_name="hero", action_name="Idle"
            )
            self.assertEqual(separated["canvas"], [47, 20])
            self.assertTrue(Path(separated["output_directory"], "imagegen-source.png").is_file())
            transformed = mcp_server.transform_frame_files(
                separated["frames"], 32, 24,
                offsets_x=[2, -2], offsets_y=[1, 1], scales=[0.5, 0.5],
                asset_name="hero", action_name="IdleAdjusted"
            )
        self.assertEqual(transformed["canvas"], [32, 24])
        self.assertEqual(len(transformed["frames"]), 2)
        for path in transformed["frames"]:
            with Image.open(path) as frame:
                self.assertEqual(frame.size, (32, 24))

    def test_transform_can_overwrite_every_original_frame(self):
        legacy = storage.LEGACY_GENERATED / "hero" / "Run"
        legacy.mkdir(parents=True)
        paths = []
        originals = []
        for index in range(2):
            path = legacy / f"frame_{index + 1:02d}.png"
            Image.new("RGBA", (20, 30), (255, index * 80, 0, 255)).save(path)
            data = path.read_bytes()
            storage.LEGACY_INVENTORY[f"generated_image/hero/Run/{path.name}"] = {
                "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            paths.append(str(path))
            originals.append(data)
        result = mcp_server.transform_frame_files(
            paths, 44, 36, offsets_x=[1, -1], scales=[0.5, 0.75],
            asset_name="hero", action_name="Run", overwrite_originals=True,
        )
        self.assertEqual(result["frameCount"], 2)
        for path, original in zip(paths, originals):
            self.assertEqual(Path(path).read_bytes(), original)
        for path in result["frames"]:
            self.assertTrue(storage.verified_file(Path(path)))
            with Image.open(path) as frame:
                self.assertEqual(frame.size, (44, 36))


if __name__ == "__main__":
    unittest.main()
