import json
import tempfile
import time
import unittest
import wave
from pathlib import Path
from fastapi.testclient import TestClient
from audioforge.api import create_app

class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); root=Path(self.temp.name)
        self.client=TestClient(create_app(root/"queue.sqlite3",root/"artifacts","mock")); self.client.__enter__()
    def tearDown(self):self.client.__exit__(None,None,None);self.temp.cleanup()
    def wait(self,job_id):
        for _ in range(100):
            data=self.client.get(f"/v1/jobs/{job_id}").json()
            if data["status"] in {"completed","failed","cancelled"}:return data
            time.sleep(.02)
        self.fail("job timeout")
    def test_health_and_readiness(self):
        self.assertTrue(self.client.get("/health").json()["ok"]);self.assertTrue(self.client.get("/ready").json()["ready"])
    def test_idempotent_job_produces_wav_and_metadata(self):
        payload={"prompt":"short wooden impact","duration_seconds":.25,"seed":7,"kind":"music","title":"Stage cue","use_case":"stage exploration","brief":{"intent":"calm exploration"},"prompt_version":"2"}
        first=self.client.post("/v1/jobs",json=payload,headers={"Idempotency-Key":"same"})
        second=self.client.post("/v1/jobs",json=payload,headers={"Idempotency-Key":"same"})
        self.assertEqual(first.json()["id"],second.json()["id"]);self.assertFalse(second.json()["created"])
        done=self.wait(first.json()["id"]);self.assertEqual(done["status"],"completed")
        audio=self.client.get(f'/v1/artifacts/{done["artifact_id"]}');self.assertEqual(audio.status_code,200)
        with tempfile.NamedTemporaryFile(suffix=".wav") as handle:
            handle.write(audio.content);handle.flush()
            with wave.open(handle.name,"rb") as wav:self.assertEqual((wav.getframerate(),wav.getnchannels()),(44100,2))
        metadata=self.client.get(f'/v1/artifacts/{done["artifact_id"]}/metadata').json()
        self.assertEqual(metadata["seed"],7);self.assertEqual(metadata["backend"],"mock");self.assertTrue(metadata["synthetic_test_artifact"]);self.assertEqual(metadata["model"],"mock-tone-contract")
        self.assertEqual(metadata["title"],"Stage cue");self.assertEqual(metadata["brief"],{"intent":"calm exploration"});self.assertEqual(metadata["prompt_version"],"2")
    def test_cancel_queued_job(self):
        response=self.client.post("/v1/jobs",json={"prompt":"cancel me","duration_seconds":1}).json()
        cancelled=self.client.delete(f'/v1/jobs/{response["id"]}').json()
        self.assertTrue(cancelled["cancel_requested"])
    def test_loop_is_separate_contract(self):
        self.assertEqual(self.client.post("/v1/postprocess/loop",json={"artifact_id":"x"}).status_code,501)

if __name__=="__main__":unittest.main()
