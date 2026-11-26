import base64
import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("portrait_native", Path(__file__).with_name("portrait-native.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class CliDiagnosticTest(unittest.TestCase):
    def test_distinguishes_cli_and_validation_without_exposing_receipts(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            self.assertEqual(module.cli_failure_status(root), 22)
            job = root / "native-cli" / "node.image.fixture"
            job.mkdir(parents=True)
            exit_receipt = job / "process-exit.json"
            response = job / "response.json"
            def exited(code):
                exit_receipt.write_text(json.dumps({"kind": "local-processes", "outcome": "joined", "returnCodes": [code]}))
            exited(1)
            self.assertEqual(module.cli_failure_status(root), 23)
            exited(0)
            self.assertEqual(module.cli_failure_status(root), 24)
            response.write_text('{"state":"blocked","error":"untrusted model text"}')
            self.assertEqual(module.cli_failure_status(root), 25)
            response.write_text('{"state":"generated","path":"/not/exposed"}')
            self.assertEqual(module.cli_failure_status(root), 26)
            response.write_text("}" * 60001)
            self.assertEqual(module.cli_failure_status(root), 24)


    def test_opaque_prop_uses_exact_staged_file_as_provider_image_reference(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            raw = b"reference fixture bytes"
            sha = hashlib.sha256(raw).hexdigest()
            data = {"propId": "new-bell-tool", "reference": {
                "assetId": sha + ".png", "sha256": sha, "base64": base64.b64encode(raw).decode()}}
            class Pairs:
                @staticmethod
                def reference(data, ref_id, directory, entry):
                    assert ref_id == "new-bell-tool" and entry["sha256"] == sha
                    directory.mkdir()
                    path = directory / entry["assetId"]
                    path.write_bytes(data)
                    return {"references": [{"localPath": str(path)}], "manifestHash": "fixture"}
            manifest = module.prepare_reference(data, Pairs, root, green_prop=True)
            desc = module.provider_descriptor("Use the existing body, add a bell.", manifest, Pairs, opaque=True)
            args = desc["arguments"]
            self.assertIs(args["transparent_background"], False)
            self.assertEqual(len(args["referenced_image_paths"]), 1)
            self.assertEqual(Path(args["referenced_image_paths"][0]).read_bytes(), raw)
            self.assertEqual(desc["metadata"]["referenceManifest"], manifest)
            self.assertNotIn("referenced_image_paths",
                module.provider_descriptor("Plain isolated prop.", None, Pairs, opaque=True)["arguments"])
            data["reference"]["base64"] = base64.b64encode(b"changed").decode()
            with self.assertRaisesRegex(ValueError, "IMAGE_NATIVE_REFERENCE_INVALID"):
                module.prepare_reference(data, Pairs, root, green_prop=True)


if __name__ == "__main__":
    unittest.main()
