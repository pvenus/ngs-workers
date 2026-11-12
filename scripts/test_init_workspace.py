import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).with_name('init-workspace.py')
spec = importlib.util.spec_from_file_location('workspace_init', SCRIPT)
init = importlib.util.module_from_spec(spec)
spec.loader.exec_module(init)


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.parent = Path(self.temp.name).resolve()
        self.root = self.parent / "new worker's workspace"

    def tearDown(self):
        self.temp.cleanup()

    def test_plan_has_no_writes(self):
        result = init.initialize(self.root)
        self.assertEqual(result['state'], 'plan-only')
        self.assertFalse(self.root.exists())

    def test_create_exact_layout_no_database_or_models(self):
        old_mask = os.umask(0o077)
        try:
            result = init.initialize(self.root, True)
        finally:
            os.umask(old_mask)
        self.assertFalse(result['readyForGeneration'])
        for name, mode in result['directories'].items():
            self.assertEqual(stat.S_IMODE((self.root / name).stat().st_mode), int(mode, 8))
        for name in result['environmentFiles']:
            self.assertEqual(stat.S_IMODE((self.root / name).stat().st_mode), 0o600)
        files = {p.relative_to(self.root).as_posix() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(files, {'config/audioforge.env', 'config/imageforge.env',
                                'config/workspace.json', 'data/imageforge/legacy/legacy_inventory.json'})
        self.assertFalse((self.root / 'runtime/acestep').exists())
        self.assertFalse((self.root / 'data/audioforge/queue.sqlite3').exists())

    def test_existing_workspace_unchanged(self):
        init.initialize(self.root, True)
        marker = self.root / 'data/keep-me'
        marker.write_bytes(b'original')
        before = {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        with self.assertRaisesRegex(ValueError, 'already exists'):
            init.initialize(self.root, True)
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_symlink_and_source_tree_denied(self):
        alias = self.parent / 'alias'
        alias.symlink_to(self.parent, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlinks'):
            init.initialize(alias / 'new', True)
        with self.assertRaisesRegex(ValueError, 'overlap'):
            init.initialize(init.SOURCE / 'new-workspace', True)

    def test_relative_and_parent_traversal_denied(self):
        for value in ('relative', str(self.parent / '..' / 'new')):
            with self.assertRaisesRegex(ValueError, 'absolute'):
                init.initialize(value, True)

    def test_shell_quoting_and_four_role_checks(self):
        init.initialize(self.root, True)
        for role in ('generation', 'catalog', 'review', 'file-worker'):
            command = '. "$1"; exec "$2" -B "$3" "$4" --check'
            env = {k: v for k, v in os.environ.items() if not k.startswith(('AUDIOFORGE_', 'WORKERS_', 'ACE_'))}
            run = subprocess.run(['/bin/sh', '-c', command, 'check',
                                  str(self.root / 'config/audioforge.env'), sys.executable,
                                  str(SCRIPT.with_name('run-audioforge.py')), role],
                                 env=env, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            result = json.loads(run.stdout)
            self.assertEqual(result['dataRoot'], str(self.root / 'data/audioforge'))
            self.assertFalse(result['opensDatabase'])
        self.assertFalse((self.root / 'data/audioforge/queue.sqlite3').exists())


if __name__ == '__main__':
    unittest.main()
