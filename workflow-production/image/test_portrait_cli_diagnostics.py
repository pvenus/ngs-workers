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


if __name__ == "__main__":
    unittest.main()
