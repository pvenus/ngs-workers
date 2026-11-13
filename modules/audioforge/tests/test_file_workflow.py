import json
import tempfile
import unittest
import uuid
import wave
from pathlib import Path

from audioforge.file_workflow import BgmRequest, FileWorkflow
from audioforge.gateway_catalog import Catalog


class FakeGeneration:
    def __init__(self, artifacts):
        self.artifacts = artifacts
        self.submissions = 0
        self.polls = 0
        self.artifact_id = uuid.uuid4().hex

    def ready(self): return {"backend": "ace-step"}

    def submit(self, item):
        self.submissions += 1
        return {"id": "job-1", "status": "queued"}

    def status(self, job_id):
        self.polls += 1
        if self.polls == 1: return {"id": job_id, "status": "running"}
        folder = self.artifacts / self.artifact_id
        folder.mkdir(parents=True, exist_ok=True)
        with wave.open(str(folder / "audio.wav"), "wb") as wav:
            wav.setnchannels(2); wav.setsampwidth(2); wav.setframerate(44100)
            wav.writeframes(b"\0\0\0\0" * 4410)
        (folder / "metadata.json").write_text(json.dumps({"kind": "music", "model": "acestep-v15-turbo", "synthetic_test_artifact": False}))
        return {"id": job_id, "status": "completed", "artifact_id": self.artifact_id}


class FileWorkflowTest(unittest.TestCase):
    def test_deferred_analysis_does_not_block_new_generation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); requests = root / "requests"; states = root / "states"
            requests.mkdir(); states.mkdir()
            for aid in ("0" * 32, "f" * 32):
                item = BgmRequest(request_id=aid, title="Title BGM", use_case="title",
                                  brief={"intent": "orchestral"}, prompt="full orchestra",
                                  duration_seconds=2, seed=42, created_at="2026-09-18T00:00:00Z")
                (requests / f"{aid}.json").write_text(item.model_dump_json())
            (states / ("0" * 32 + ".json")).write_text(json.dumps({"request_id": "0" * 32, "phase": "analysis_deferred"}))
            generation = FakeGeneration(root / "artifacts")
            workflow = FileWorkflow(requests, states, generation=generation,
                                    catalog=Catalog(root / "catalog.sqlite3", root / "artifacts"),
                                    artifact_dir=root / "artifacts")
            self.assertEqual(workflow.run_once()[0]["request_id"], "f" * 32)
            self.assertEqual(generation.submissions, 1)

    def test_enqueue_register_review_and_restart_idempotency(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            requests, states, artifacts = root / "requests", root / "states", root / "artifacts"
            requests.mkdir()
            aid = uuid.uuid4().hex
            item = BgmRequest(request_id=aid, title="Stage BGM", use_case="stage", brief={"intent": "calm"},
                              prompt="soft strings", model="acestep-v15-turbo", duration_seconds=2,
                              seed=42, created_at="2026-09-18T00:00:00Z")
            (requests / f"{aid}.json").write_text(item.model_dump_json())
            generator = FakeGeneration(artifacts)
            reviews = []
            def review(artifact_id):
                reviews.append(artifact_id)
                return {"status": "evaluated", "rubric_version": "v1", "score": {"total_score": 80}, "total_score": 80}
            catalog = Catalog(root / "catalog.sqlite3", artifacts)
            swap = [0]
            workflow = FileWorkflow(requests, states, generation=generator, catalog=catalog,
                                    reviewer=review, artifact_dir=artifacts, memory_guard=lambda: swap[0])
            self.assertEqual(workflow.run_once()[0]["phase"], "submitted")
            self.assertEqual(workflow.run_once()[0]["phase"], "submitted")
            swap[0] = 2000
            self.assertEqual(workflow.run_once()[0]["phase"], "analysis_deferred")
            self.assertEqual(reviews, [])
            swap[0] = 0
            self.assertEqual(workflow.run_once()[0]["phase"], "done")
            self.assertEqual(len(catalog.list()), 1)
            self.assertEqual(catalog.list()[0]["review_status"], "completed")
            self.assertEqual(reviews, [generator.artifact_id])
            self.assertEqual(workflow.run_once(), [])
            self.assertEqual(generator.submissions, 1)

    def test_mock_backend_does_not_submit_real_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); requests = root / "requests"; requests.mkdir()
            aid = uuid.uuid4().hex
            item = BgmRequest(request_id=aid, title="BGM", use_case="stage", brief={"intent": "calm"},
                              prompt="soft music", duration_seconds=2, seed=1, created_at="2026-09-18T00:00:00Z")
            (requests / f"{aid}.json").write_text(item.model_dump_json())
            class Mock(FakeGeneration):
                def ready(self): return {"backend": "mock"}
            generation = Mock(root / "artifacts")
            workflow = FileWorkflow(requests, root / "states", generation=generation,
                                    catalog=Catalog(root / "catalog.sqlite3", root / "artifacts"),
                                    artifact_dir=root / "artifacts")
            result = workflow.run_once()[0]
            self.assertEqual(result["phase"], "new")
            self.assertIn("mock", result["last_error"])
            self.assertEqual(generation.submissions, 0)


if __name__ == "__main__": unittest.main()
