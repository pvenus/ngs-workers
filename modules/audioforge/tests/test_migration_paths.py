import os
import unittest
from pathlib import Path
from unittest.mock import patch
import audioforge.acestep_backend as backend_module
from audioforge.acestep_backend import AceStepBackend

class MigrationPathsTest(unittest.TestCase):
    def test_internal_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(AceStepBackend().root, Path(backend_module.__file__).resolve().parents[1] / "runtime/acestep")
    def test_env_override(self):
        with patch.dict(os.environ, {"AUDIOFORGE_ACESTEP_ROOT": "/tmp/ace-env-test"}):
            self.assertEqual(AceStepBackend().root, Path("/tmp/ace-env-test").resolve())
    def test_explicit_root_wins(self):
        with patch.dict(os.environ, {"AUDIOFORGE_ACESTEP_ROOT": "/tmp/ace-env-test"}):
            self.assertEqual(AceStepBackend(runtime_root="/tmp/ace-explicit-test").root, Path("/tmp/ace-explicit-test").resolve())
