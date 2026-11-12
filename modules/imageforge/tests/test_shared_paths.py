import asyncio
import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import UploadFile
from PIL import Image, ImageDraw

import server
from imageforge_mcp import mcp_server, storage


class SharedPathTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.mount = Path(self.temp.name).resolve() / "volume"
        self.mount.mkdir()
        self.root = self.mount / "MCP/resources/v1"
        self.project = Path(self.temp.name).resolve() / "legacy-project"
        self.project.mkdir()
        self.patches = [
            patch.object(storage, "MOUNT", self.mount),
            patch.dict(os.environ, {"IMAGEFORGE_RESOURCE_ROOT": str(self.root)}),
            patch.object(storage, "guard_volume"),
            patch.object(server, "DOWNLOADS_ROOT", self.root / "imageforge/jobs"),
            patch.object(server, "GENERATED_IMAGE_ROOT", self.root / "imageforge/jobs"),
            patch.object(mcp_server, "DOWNLOADS_ROOT", self.root / "imageforge/jobs"),
            patch.object(mcp_server, "GENERATED_IMAGE_ROOT", self.root / "imageforge/jobs"),
            patch.object(storage, "PROJECT_ROOT", self.project),
            patch.object(storage, "LEGACY_GENERATED", self.project / "generated_image"),
            patch.object(storage, "LEGACY_DOWNLOADS", self.project / "imageforge-downloads"),
            patch.object(storage, "LEGACY_INVENTORY", {}),
        ]
        for item in self.patches:
            item.start()
        image = Image.new("RGBA", (150, 90), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((10, 10, 49, 79), fill="red")
        draw.rectangle((100, 10, 139, 79), fill="blue")
        output = io.BytesIO()
        image.save(output, "PNG")
        self.png = output.getvalue()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def upload(self, name="sheet.png", data=None):
        return UploadFile(filename=name, file=io.BytesIO(self.png if data is None else data))

    async def test_api_and_mcp_share_root_and_readback(self):
        response = await server.external_extract_objects(
            file=self.upload(), expected_object_count=2, output_mode="save",
            asset_name="hero", action_name="Idle", horizontal_padding=20,
        )
        payload = json.loads(response.body)
        directory = Path(payload["output_directory"])
        self.assertTrue(directory.is_relative_to(storage.service_root()))
        self.assertEqual(server.GENERATED_IMAGE_ROOT, mcp_server.GENERATED_IMAGE_ROOT)
        first = directory / "frame-0.png"
        self.assertTrue(storage.verified_file(first))
        self.assertEqual(mcp_server.inspect_image(str(first))["height"], 90)
        catalog = server.list_generated_images()
        self.assertEqual(catalog["assets"][0]["actions"][0]["frame_count"], 2)
        self.assertEqual(server.resolve_generated_frame(directory.parent.name, directory.name, first.name), first)

    async def test_overwrite_request_preserves_legacy_and_creates_version(self):
        legacy = storage.LEGACY_GENERATED / "hero" / "Idle"
        legacy.mkdir(parents=True)
        frame = legacy / "frame_01.png"
        frame.write_bytes(self.png)
        storage.LEGACY_INVENTORY["generated_image/hero/Idle/frame_01.png"] = {
            "bytes": len(self.png), "sha256": hashlib.sha256(self.png).hexdigest()}
        response = await server.external_transform_frames(
            files=[self.upload("frame_01.png")], transforms="[]", canvas_width=64,
            canvas_height=64, output_mode="overwrite", asset_name="hero",
            action_name="Idle", overwrite_asset="hero", overwrite_action="Idle",
            source_filenames='["frame_01.png"]',
        )
        payload = json.loads(response.body)
        self.assertEqual(frame.read_bytes(), self.png)
        self.assertNotEqual(payload["frames"][0], str(frame))
        self.assertTrue(storage.verified_file(Path(payload["frames"][0])))
        self.assertTrue(storage.verified_file(frame))
        frame.write_bytes(b"changed")
        self.assertFalse(storage.verified_file(frame))

    async def test_direct_zip_does_not_create_root(self):
        response = await server.external_extract_objects(
            file=self.upload(), expected_object_count=2, output_mode="direct",
            asset_name="hero", action_name="Idle", horizontal_padding=20,
        )
        self.assertEqual(response.media_type, "application/zip")
        self.assertFalse((self.mount / "MCP").exists())

    async def test_mcp_writes_verified_version(self):
        source = Path(self.temp.name) / "source.png"
        source.write_bytes(self.png)
        result = mcp_server.remove_image_background(str(source), tolerance=0, feather=0)
        path = Path(result["output_path"])
        self.assertTrue(path.is_relative_to(storage.service_root()))
        self.assertTrue(storage.verified_file(path))


if __name__ == "__main__":
    unittest.main()
