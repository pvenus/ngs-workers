import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).with_name('run-audioforge.py')

class LauncherTests(unittest.TestCase):
    def run_check(self, role, data=None, backend=None):
        env = {k:v for k,v in os.environ.items() if not k.startswith('AUDIOFORGE_')}
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        if data is not None: env['AUDIOFORGE_DATA_DIR'] = data
        if backend is not None: env['AUDIOFORGE_BACKEND'] = backend
        return subprocess.run([sys.executable, '-B', str(SCRIPT), role, '--check'], env=env, capture_output=True, text=True)
    def test_all_roles_check_without_data_creation(self):
        with tempfile.TemporaryDirectory() as folder:
            data = str(Path(folder)/'not-created')
            for role in ('generation','catalog','review','file-worker'):
                p=self.run_check(role,data,'ace-step');self.assertEqual(p.returncode,0,p.stderr)
                v=json.loads(p.stdout);self.assertFalse(v['startsWorker']);self.assertFalse(v['opensDatabase']);self.assertFalse(v['loadsModel'])
                self.assertFalse(Path(data).exists())
    def test_missing_explicit_data_denied(self):
        self.assertNotEqual(self.run_check('file-worker').returncode,0)
    def test_implicit_mock_generation_denied(self):
        self.assertNotEqual(self.run_check('generation','/tmp/audioforge-test-not-created').returncode,0)
    def test_source_tree_data_denied(self):
        self.assertNotEqual(self.run_check('file-worker',str(SCRIPT.parents[1]/'data')).returncode,0)

if __name__ == '__main__': unittest.main()
