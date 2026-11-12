"""HQ admission tests: temporary databases only, never load a model."""
import threading
import types
import sys
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from audioforge.api import create_app
from audioforge.hq_contract import MODEL
from audioforge.file_workflow import BgmRequest, FileWorkflow
from pydantic import ValidationError


class HQHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.app = create_app(root / 'queue.db', root / 'artifacts', 'mock')
        # No lifespan: no background worker or model execution.
        self.client = TestClient(self.app)
        self.payload = dict(prompt='Instrumental battle music', duration_seconds=10,
                            seed=42, kind='music', model=MODEL,
                            generation_settings={'instrumental': True})

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def post(self, payload=None, key='hq-test'):
        return self.client.post('/v1/jobs', json=payload or self.payload,
                                headers={'Idempotency-Key': key})

    def count(self):
        with self.app.state.store.connect() as db:
            return db.execute('SELECT count(*) FROM jobs').fetchone()[0]

    def test_concurrent_store_submission_one_job(self):
        from audioforge.store import Store
        second = Store(self.app.state.store.path)
        results, errors = [], []
        barrier = threading.Barrier(2)
        def submit(store):
            try:
                barrier.wait()
                results.append(store.submit(self.payload, 'race'))
            except Exception as exc:
                errors.append(exc)
        with patch.dict(os.environ, {'AUDIOFORGE_HQ_ENABLED': '1'}):
            threads = [threading.Thread(target=submit, args=(store,))
                       for store in (self.app.state.store, second)]
            for thread in threads: thread.start()
            for thread in threads: thread.join(10)
        self.assertEqual(errors, [])
        self.assertEqual(sorted(created for _, created in results), [False, True])
        self.assertEqual(self.count(), 1)

    def test_worker_hq_lazy_routing_without_model(self):
        worker = self.app.state.worker
        captured = {}
        class FakeBackend:
            name = 'hq-fake'
            def __init__(self, **kwargs):
                captured.update(kwargs)
            def generate(self, prompt, duration, seed, model, output, cancelled, settings):
                captured['model'] = model
                captured['settings'] = settings
                output.parent.mkdir(parents=True)
                worker.stop_event.set()
                return {'synthetic_test_artifact': True}
        fake_module = types.ModuleType('audioforge.hq_backend')
        fake_module.HQBackend = FakeBackend
        with patch.dict(os.environ, {'AUDIOFORGE_HQ_ENABLED': '1',
                                     'AUDIOFORGE_ACESTEP_ROOT': '/tmp/mock-only-runtime'}):
            job = self.post().json()
            with patch.dict(sys.modules, {'audioforge.hq_backend': fake_module}), \
                 patch('audioforge.service.make_backend', side_effect=AssertionError('no fallback')):
                worker.run()
        self.assertEqual(captured['model'], MODEL)
        self.assertEqual(captured['artifact_root'], worker.artifacts)
        self.assertEqual(self.app.state.store.get(job['id'])['status'], 'completed')

    def test_disabled_replay_and_conflict(self):
        with patch.dict(os.environ, {'AUDIOFORGE_HQ_ENABLED': '0'}):
            self.assertEqual(self.post().status_code, 503)
            self.assertEqual(self.count(), 0)
        with patch.dict(os.environ, {'AUDIOFORGE_HQ_ENABLED': '1'}):
            first = self.post()
            self.assertEqual(first.status_code, 202, first.text)
            self.assertEqual(first.json()['max_attempts'], 1)
        with patch.dict(os.environ, {'AUDIOFORGE_HQ_ENABLED': '0'}):
            self.assertEqual(self.post().status_code, 200)
            changed = {**self.payload, 'seed': 43}
            self.assertEqual(self.post(changed).status_code, 409)
            self.assertEqual(self.post(key='new').status_code, 503)
        self.assertEqual(self.count(), 1)

    def test_raw_invalid_before_enqueue(self):
        variants = [dict(seed=True), dict(seed='42'), dict(seed=42.0),
                    dict(duration_seconds=True), dict(duration_seconds='10'),
                    dict(duration_seconds=9), dict(kind='sfx'), dict(unknown=True),
                    dict(generation_settings={'instrumental': 1}),
                    dict(generation_settings={'bpm': True}),
                    dict(generation_settings={'bpm': 120.0}),
                    dict(generation_settings={'instrumental': False}),
                    dict(generation_settings={'temperature': .5})]
        with patch.dict(os.environ, {'AUDIOFORGE_HQ_ENABLED': '1'}):
            for change in variants:
                with self.subTest(change=change):
                    response = self.post({**self.payload, **change})
                    self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.count(), 0)

    def test_nested_exact_types_conflict(self):
        with patch.dict(os.environ, {'AUDIOFORGE_HQ_ENABLED': '1'}):
            p = {**self.payload, 'brief': {'nested': [True, 1, 1.0]}}
            self.assertEqual(self.post(p).status_code, 202)
            for values in ([1, 1, 1.0], [True, 1.0, 1.0]):
                self.assertEqual(self.post({**p, 'brief': {'nested': values}}).status_code, 409)

    def test_recovery_no_retry_and_existing_queue_preserved(self):
        store = self.app.state.store
        with patch.dict(os.environ, {'AUDIOFORGE_HQ_ENABLED': '1'}):
            hq = self.post().json()
        old, _ = store.submit(dict(prompt='test', model='small-sfx'), 'old')
        store._update(hq['id'], status='running', attempts=1)
        store._update(old['id'], status='running', attempts=1)
        store.recover_running()
        self.assertEqual(store.get(hq['id'])['status'], 'failed')
        self.assertIn('RECONCILIATION', store.get(hq['id'])['error'])
        self.assertEqual(store.get(old['id'])['status'], 'queued')
        store.fail_or_retry(hq['id'], 'failed before loading')
        self.assertEqual(store.get(hq['id'])['status'], 'failed')

    def test_readiness_not_claiming_runtime_verified(self):
        for flag in ('0', '1'):
            with patch.dict(os.environ, {'AUDIOFORGE_HQ_ENABLED': flag}):
                value = self.client.get('/ready').json()
                self.assertTrue(value['ready'])  # Existing profile semantics preserved.
                self.assertEqual(value['profiles'][MODEL]['enabled'], flag == '1')
                self.assertFalse(value['profiles'][MODEL]['runtime_verified'])

    def test_file_raw_validator(self):
        item = dict(schema_version=1, request_id='a'*32, title='test', use_case='test',
                    brief={'intent': 'test'}, created_at='2026-01-01', **self.payload)
        item.pop('kind')
        self.assertEqual(BgmRequest.model_validate(item).model, MODEL)
        for change in ({'seed': True}, {'duration_seconds': '10'},
                       {'generation_settings': {'instrumental': 1}}):
            with self.assertRaises(ValidationError):
                BgmRequest.model_validate({**item, **change})

    def test_file_disabled_does_not_post(self):
        item = dict(request_id='b'*32, title='test', use_case='test', brief={'x': 1},
                    created_at='2026-01-01', **self.payload)
        item.pop('kind')
        class Client:
            def ready(self):
                return {'ready': True, 'backend': 'ace', 'profiles': {MODEL: {'enabled': False}}}
            def submit(self, item):
                raise AssertionError('disabled HQ must not POST')
        workflow = object.__new__(FileWorkflow)
        workflow.generation = Client()
        workflow.allow_mock = False
        state = {'phase': 'new'}
        self.assertIsNone(workflow.advance(BgmRequest.model_validate(item), state))
        self.assertEqual(state['phase'], 'new')


if __name__ == '__main__':
    unittest.main()
