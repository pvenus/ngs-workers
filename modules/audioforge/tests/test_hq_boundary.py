"""Real pinned-code integration with disposable metadata and no model execution."""
import copy
import fcntl
import hashlib
import importlib
import importlib.util
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[3]


class HQBoundaryFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.home())
        self.root = Path(self.temp.name).resolve()
        self.release = self.root / 'release'
        names = ['workers/__init__.py', 'workers/audio/__init__.py',
                 'workers/audio/boundary.py', 'workers/audio/legacy_backend.py',
                 'workers/audio/hq_bridge.py', *['modules/audioforge/audioforge/' + n for n in
                 ('__init__.py', 'backends.py', 'hq_contract.py', 'hq_backend.py', 'hq_pins.json', 'ace_hq_runner.py')]]
        for name in names:
            dest = self.release / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(SOURCE / name, dest)
        runtime = self.root / 'runtime'
        python = runtime / 'source/.venv/bin/python'
        python.parent.mkdir(parents=True)
        python.write_text('non-executable interpreter fixture\n')
        venv = runtime / 'source/.venv/pyvenv.cfg'
        venv.write_text('fixture only\n')
        artifacts = self.root / 'artifacts'
        self.artifact = artifacts / ('content.' + 'a' * 40)
        self.artifact.mkdir(parents=True)
        def identity(path):
            st = path.stat()
            return {'path': str(path), 'device': st.st_dev, 'inode': st.st_ino}
        locks = []
        for name in ('shared.lock', 'legacy.lock'):
            path = self.root / name
            path.write_text('')
            path.chmod(0o600)
            locks.append(identity(path))
        pins = {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in [*[self.release / n for n in names], python, venv]}
        self.assertEqual(len(pins), 13)
        self.config = dict(schema='private.music-hq-boundary.v1', release_root=str(self.release),
                           runtime_root=str(runtime), artifact_root=str(artifacts),
                           runner_path=str(self.release / 'modules/audioforge/audioforge/ace_hq_runner.py'),
                           python_path=str(python), directory_pins={'runtime_root': identity(runtime), 'artifact_root': identity(artifacts)},
                           shared_lock=locks[0], legacy_lock=locks[1], file_pins=pins)
        self.before_modules = set(sys.modules)
        self.namespace = '_hq_boundary_fixture_' + hashlib.sha256(str(self.root).encode()).hexdigest()
        spec = importlib.util.spec_from_file_location(self.namespace, self.release / 'workers/__init__.py',
                                                      submodule_search_locations=[str(self.release / 'workers')])
        module = importlib.util.module_from_spec(spec)
        sys.modules[self.namespace] = module
        spec.loader.exec_module(module)
        self.boundary = importlib.import_module(self.namespace + '.audio.boundary')
        self.bridge = importlib.import_module(self.namespace + '.audio.hq_bridge')
        self.paths, _ = self.boundary.validate_config(self.config, self.artifact)
        self.backend = self.bridge._package(self.config, self.paths)

    def tearDown(self):
        for name in set(sys.modules) - self.before_modules:
            if name.startswith((self.namespace, '_workers_hq_')):
                sys.modules.pop(name, None)
        self.temp.cleanup()

    def call(self):
        return self.boundary.generate_music(self.config, self.artifact, 'fixture only', 10, 42,
            'acestep-xl-sft-lm4b', self.artifact / 'not-created.wav', lambda: False, settings={})

    def test_actual_constructor_and_both_locks(self):
        boundary, config = self.boundary, self.config
        def fake_generate(backend, *args, **kwargs):
            self.assertIs(backend.activation, True)
            self.assertEqual(len(backend.lock_fds), 2)
            backend.lock_validator()
            for fd, key in zip(backend.lock_fds, ('shared_lock', 'legacy_lock')):
                boundary.validate_lock_fd(fd, config[key])
            return {'fixtureOnly': True}
        with patch.object(self.backend, 'verify_runtime', return_value={}) as verify, \
             patch.object(self.backend, 'generate', fake_generate):
            self.assertTrue(boundary.hq_available(config))
            verify.assert_called_once_with(self.paths['runtime_root'], deep=False)
            self.assertEqual(self.call(), {'fixtureOnly': True})
        # Both locks are released after the completed boundary call.
        for key in ('shared_lock', 'legacy_lock'):
            fd = boundary.open_existing_lock(config[key])
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(fd)

    def test_busy_and_cancel_translation_without_spawn(self):
        fd = self.boundary.open_existing_lock(self.config['shared_lock'])
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.object(self.backend, 'generate', side_effect=AssertionError('busy must not execute')):
                with self.assertRaisesRegex(RuntimeError, 'SHARED_BUSY_NO_RETRY'):
                    self.call()
        finally:
            os.close(fd)
        package = importlib.import_module(self.backend.__module__)
        boundary_cancelled = importlib.import_module(self.namespace + '.audio').Cancelled
        with patch.object(self.backend, 'generate', side_effect=package.Cancelled()):
            with self.assertRaises(boundary_cancelled):
                self.call()
        with patch.object(self.backend, 'generate', side_effect=ValueError('fixture-original-error')):
            with self.assertRaisesRegex(ValueError, 'fixture-original-error'):
                self.call()

    def test_v1_exact_seven_pins_and_hq_tamper_denial(self):
        config = copy.deepcopy(self.config)
        config['schema'] = 'private.music-boundary.v1'
        runner = self.release / 'workers/audio/legacy_runner.py'
        shutil.copyfile(SOURCE / 'workers/audio/legacy_runner.py', runner)
        config['runner_path'] = str(runner)
        keep = [runner, self.paths['python_path'], self.paths['runtime_root'] / 'source/.venv/pyvenv.cfg',
                *[self.release / n for n in ('workers/__init__.py', 'workers/audio/__init__.py',
                                            'workers/audio/boundary.py', 'workers/audio/legacy_backend.py')]]
        config['file_pins'] = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in keep}
        self.assertEqual(len(config['file_pins']), 7)
        self.boundary.validate_config(config, self.artifact)
        extra = copy.deepcopy(config)
        extra['file_pins'][str(self.release / 'workers/audio/hq_bridge.py')] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'MISSING_CODE_PIN'):
            self.boundary.validate_config(extra, self.artifact)
        (self.release / 'modules/audioforge/audioforge/hq_pins.json').write_text('tampered')
        self.assertFalse(self.boundary.hq_available(self.config))


if __name__ == '__main__':
    unittest.main()
