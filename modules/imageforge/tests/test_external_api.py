import io
import os
import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

try:
    from fastapi.testclient import TestClient
except (ImportError, RuntimeError):
    TestClient = None
from PIL import Image, ImageDraw

import server
from imageforge_mcp import storage


@unittest.skipIf(TestClient is None, "httpx is unavailable; direct API tests run in test_shared_paths.py")
class ExternalApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.mount = self.root / "volume"
        self.mount.mkdir()
        self.shared = self.mount / "MCP/resources/v1"
        self.project = self.root / "legacy"
        self.project.mkdir()
        self.generated = self.project / "generated_image"
        self.patches = [
            patch.object(storage, "MOUNT", self.mount),
            patch.dict(os.environ, {"IMAGEFORGE_RESOURCE_ROOT": str(self.shared)}),
            patch.object(storage, "guard_volume"),
            patch.object(storage, "PROJECT_ROOT", self.project),
            patch.object(storage, "LEGACY_GENERATED", self.generated),
            patch.object(storage, "LEGACY_DOWNLOADS", self.project / "imageforge-downloads"),
            patch.object(storage, "LEGACY_INVENTORY", {}),
            patch.object(server, "DOWNLOADS_ROOT", self.shared / "imageforge/jobs"),
            patch.object(server, "GENERATED_IMAGE_ROOT", self.shared / "imageforge/jobs"),
        ]
        for item in self.patches:
            item.start()
        image = Image.new("RGBA", (150, 90), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((10, 10, 49, 79), fill="red")
        draw.rectangle((100, 10, 139, 79), fill="blue")
        buffer = io.BytesIO()
        image.save(buffer, "PNG")
        self.png = buffer.getvalue()
        self.client = TestClient(server.app)

    def tearDown(self):
        self.client.close()
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def freeze_legacy(self):
        for path in self.generated.rglob("*"):
            if path.is_file():
                data = path.read_bytes()
                storage.LEGACY_INVENTORY[path.relative_to(self.project).as_posix()] = {
                    "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}

    def test_returns_png_list_when_expected_count_matches(self):
        response = self.client.post(
            "/api/external/extract-objects",
            files={"file": ("sample.png", self.png, "image/png")},
            data={"expected_object_count": "2"},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["object_count"], 2)
        self.assertEqual(payload["selected_settings"], {"background_tolerance": 4, "alpha_threshold": 150})
        self.assertEqual(payload["layout"], {"row_count": 1, "source_height": 90, "frame_height": 90})
        self.assertEqual(len(payload["png_list"]), 2)
        self.assertEqual({item["height"] for item in payload["png_list"]}, {90})
        self.assertEqual({item["width"] for item in payload["png_list"]}, {80})
        output = Path(payload["output_directory"])
        self.assertEqual(output.name, "Frames")
        self.assertTrue((output / "imagegen-source.png").is_file())
        self.assertTrue((output / "frame-0.png").is_file())
        self.assertTrue((output / "frame-manifest.json").is_file())
        for path in output.glob("frame-*.png"):
            with Image.open(path) as frame:
                bbox = frame.getchannel("A").getbbox()
                self.assertGreater(bbox[0], 0)
                self.assertLess(bbox[2], frame.width)

    def test_returns_attempts_when_expected_count_is_not_found(self):
        response = self.client.post(
            "/api/external/extract-objects",
            files={"file": ("sample.png", self.png, "image/png")},
            data={"expected_object_count": "3"},
        )
        self.assertEqual(response.status_code, 422)
        payload = response.json()
        self.assertEqual(payload["error"], "EXPECTED_OBJECT_COUNT_NOT_FOUND")
        self.assertEqual(len(payload["attempts"]), 24)

    def test_two_rows_use_half_source_height(self):
        image = Image.new("RGBA", (150, 180), "white")
        draw = ImageDraw.Draw(image)
        for box in ((10, 10, 49, 79), (100, 10, 139, 79), (10, 100, 49, 169), (100, 100, 139, 169)):
            draw.rectangle(box, fill="black")
        buffer = io.BytesIO()
        image.save(buffer, "PNG")
        response = self.client.post(
            "/api/external/extract-objects",
            files={"file": ("two-rows.png", buffer.getvalue(), "image/png")},
            data={"expected_object_count": "4"},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["layout"]["row_count"], 2)
        self.assertEqual(payload["layout"]["frame_height"], 90)
        self.assertEqual({item["height"] for item in payload["png_list"]}, {90})

    def test_direct_extract_returns_memory_zip_without_saving(self):
        response = self.client.post(
            "/api/external/extract-objects",
            files={"file": ("sample.png", self.png, "image/png")},
            data={"expected_object_count": "2", "output_mode": "direct"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "application/zip")
        self.assertFalse(server.GENERATED_IMAGE_ROOT.exists())
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            self.assertEqual(set(archive.namelist()), {"manifest.json", "frame-0.png", "frame-1.png"})

    def test_missing_mount_proof_returns_503_without_mkdir(self):
        with patch.object(storage, "guard_volume", side_effect=storage.StorageUnavailable("unverified mount")):
            response = self.client.post(
                "/api/external/extract-frames",
                files={"file": ("sample.png", self.png, "image/png")},
                data={"expected_object_count": "2"},
            )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"], "STORAGE_UNAVAILABLE")
        self.assertFalse((self.mount / "MCP").exists())

    def test_transform_frames_is_separate_and_supports_direct_zip(self):
        response = self.client.post(
            "/api/external/transform-frames",
            files=[
                ("files", ("frame-0.png", self.png, "image/png")),
                ("files", ("frame-1.png", self.png, "image/png")),
            ],
            data={
                "canvas_width": "200",
                "canvas_height": "120",
                "output_mode": "direct",
                "transforms": '[{"x":5,"y":-3,"scale":0.5},{"x":0,"y":0,"scale":1.0}]',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "application/zip")
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            with Image.open(io.BytesIO(archive.read("frame-0.png"))) as frame:
                self.assertEqual(frame.size, (200, 120))

    def test_transform_overwrites_entire_generated_frame_list(self):
        action = self.generated / "hero" / "Idle"
        action.mkdir(parents=True)
        names = ["frame_01.png", "frame_02.png"]
        originals = []
        for index, name in enumerate(names):
            image = Image.new("RGBA", (20 + index * 4, 30), (255, 0, 0, 255))
            image.save(action / name)
            buffer = io.BytesIO()
            image.save(buffer, "PNG")
            originals.append(buffer.getvalue())
        self.freeze_legacy()
        response = self.client.post(
            "/api/external/transform-frames",
            files=[("files", (name, data, "image/png")) for name, data in zip(names, originals)],
            data={
                "canvas_width": "48", "canvas_height": "40", "output_mode": "overwrite",
                "overwrite_asset": "hero", "overwrite_action": "Idle",
                "source_filenames": json.dumps(names),
                "transforms": '[{"x":2,"y":0,"scale":0.5},{"x":-2,"y":1,"scale":0.75}]',
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["overwritten"])
        self.assertTrue(payload["versioned_from"])
        self.assertEqual(len(payload["frames"]), 2)
        for name, original in zip(names, originals):
            self.assertEqual((action / name).read_bytes(), original)
        for path in payload["frames"]:
            with Image.open(path) as frame:
                self.assertEqual(frame.size, (48, 40))
        manifest = json.loads((Path(payload["output_directory"]) / "transform-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["frameCount"], 2)
        self.assertEqual(len(manifest["appliedTransforms"]), 2)
        self.assertEqual(manifest["transforms"], [{"x": 0, "y": 0, "scale": 1.0}] * 2)

    def test_lists_and_serves_generated_image_folder_frames(self):
        action = self.generated / "black_cloth_raider" / "Stun"
        action.mkdir(parents=True)
        for index in range(1, 3):
            Image.new("RGBA", (64, 80), (255, 0, 0, 255)).save(action / f"frame_{index:02d}.png")
        (action / "transform-manifest.json").write_text(
            '{"canvas":[64,80],"transforms":[{"x":2,"y":-1,"scale":0.9}]}', encoding="utf-8"
        )
        self.freeze_legacy()
        response = self.client.get("/api/external/generated-images")
        self.assertEqual(response.status_code, 200)
        entry = response.json()["assets"][0]["actions"][0]
        self.assertEqual(entry["action"], "Stun")
        self.assertEqual(entry["frame_count"], 2)
        self.assertEqual(entry["canvas"], [64, 80])
        frame = self.client.get(entry["frames"][0]["url"])
        self.assertEqual(frame.status_code, 200)
        self.assertEqual(frame.headers["content-type"], "image/png")

    def test_generated_frame_route_rejects_traversal(self):
        response = self.client.get("/api/external/generated-images/../secret/Frames/frame-0.png")
        self.assertIn(response.status_code, (404, 422))

    def test_background_editor_overwrites_only_selected_frame(self):
        action = self.generated / "hero" / "Idle"
        action.mkdir(parents=True)
        target = action / "frame_01.png"
        image = Image.new("RGBA", (20, 20), "white")
        ImageDraw.Draw(image).rectangle((5, 5, 14, 14), fill="red")
        image.save(target)
        self.freeze_legacy()
        opened = self.client.post(
            "/api/external/background-editor/open",
            json={"asset": "hero", "action": "Idle", "filename": "frame_01.png"},
        )
        self.assertEqual(opened.status_code, 200)
        session_id = opened.json()["session_id"]
        edited = self.client.post(
            f"/api/images/{session_id}/remove-background",
            json={"x": 0, "y": 0, "tolerance": 0, "contiguous": True, "feather": 0},
        )
        self.assertEqual(edited.status_code, 200)
        with Image.open(target) as before_save:
            self.assertEqual(before_save.getpixel((0, 0))[3], 255)
        saved = self.client.post(f"/api/external/background-editor/{session_id}/overwrite")
        self.assertEqual(saved.status_code, 200)
        with Image.open(target) as original:
            self.assertEqual(original.getpixel((0, 0))[3], 255)
        self.assertNotEqual(saved.json()["path"], str(target))
        with Image.open(saved.json()["path"]) as result:
            self.assertEqual(result.getpixel((0, 0))[3], 0)
            self.assertEqual(result.getpixel((10, 10))[3], 255)


if __name__ == "__main__":
    unittest.main()
