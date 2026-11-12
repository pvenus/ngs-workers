import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from videoforge_mcp import jobs

class JobTests(unittest.TestCase):
    def test_atomic_job_round_trip(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(jobs, "JOB_DIR", Path(directory)):
            jobs.write_job("abc123", {"status": "queued"})
            self.assertEqual(jobs.read_job("abc123"), {"status": "queued"})

    def test_rejects_path_traversal(self):
        with self.assertRaises(ValueError):
            jobs.job_path("../oops")

if __name__ == "__main__":
    unittest.main()
