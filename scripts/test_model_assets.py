import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('model_assets', Path(__file__).with_name('model-assets.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def artifact(data=b'fixture bytes'):
    return {'path': 'checkpoints/test.bin', 'url': 'https://huggingface.co/fixture/resolve/' + 'a' * 40 + '/test.bin',
            'size': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
            'gitBlobSha1': hashlib.sha1(('blob ' + str(len(data)) + '\0').encode() + data).hexdigest()}


class Reply(io.BytesIO):
    def __init__(self, data, status=200, headers=None):
        super().__init__(data)
        self.status = status
        self.headers = {'Content-Length': str(len(data)), **(headers or {})}


class ModelAssetTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()

    def tearDown(self):
        self.temporary.cleanup()

    def test_default_is_offline_and_does_not_create_destination(self):
        dest = self.root / 'planned'
        with patch.object(m, 'network', side_effect=AssertionError('network forbidden')), contextlib.redirect_stdout(io.StringIO()) as out:
            m.main(['--destination', str(dest)])
        result = json.loads(out.getvalue())
        self.assertEqual(result['state'], 'dry-run')
        self.assertEqual(result['profile'], 'acestep-xl-sft-lm4b')
        self.assertFalse(result['currentGenerationAdapterSupported'])
        self.assertTrue(result['sourceGenerationAdapterImplemented'])
        self.assertFalse(result['liveGenerationValidated'])
        self.assertFalse(dest.exists())

    def test_high_quality_profile_is_distinct_from_compatibility_baseline(self):
        manifest, _ = m.load_manifest()
        hq = m.select(manifest, 'acestep-xl-sft-lm4b')
        baseline = m.select(manifest, 'acestep-turbo')
        self.assertTrue(hq['preferred'])
        self.assertFalse(baseline['preferred'])
        self.assertIn('acestep-5Hz-lm-4B', hq['components'])
        self.assertNotIn('acestep-5Hz-lm-4B', baseline['components'])
        self.assertEqual(hq['artifactTensorMetadata']['acestep-v15-xl-sft']['dtype'], 'F32')
        sync = [x for x in hq['files'] if x['origin'] == 'official-pinned-runtime-code-sync']
        self.assertEqual(len(sync), 3)
        self.assertEqual(len({x['path'] for x in hq['files']}), len(hq['files']))
        lfs = [x for x in hq['files'] if x.get('integrityBasis') == 'hf-lfs-sha256']
        self.assertEqual(len(lfs), 9)
        self.assertTrue(all(x['gitBlobSha1'] is None for x in lfs))
        lfs[0]['gitBlobSha1'] = lfs[0]['gitLfsPointerBlobSha1']
        with self.assertRaisesRegex(ValueError, 'LFS_POINTER_IS_NOT_ARTIFACT_CONTENT_HASH'):
            m.select(manifest, 'acestep-xl-sft-lm4b')

    def test_held_profiles_and_unsafe_paths_rejected(self):
        manifest, _ = m.load_manifest()
        for name in ['video', 'qwen25-omni-review', 'stable-audio-legacy', 'imageforge-http']:
            with self.assertRaisesRegex(ValueError, 'PROFILE_NOT_DOWNLOADABLE'):
                m.select(manifest, name)
        for path in ['/outside', '../outside', 'a/../outside']:
            with self.assertRaises(ValueError):
                m.relative(path)
        for url in ['http://huggingface.co/a', 'https://evil.invalid/a', 'https://user:password@huggingface.co/a']:
            with self.assertRaises(ValueError):
                m.allowed_url(url, redirect=True)
        self.assertEqual(m.allowed_url('https://cas-bridge.xethub.hf.co/a?signed=value', redirect=True), 'https://cas-bridge.xethub.hf.co/a?signed=value')

    def test_verified_download_and_existing_skip_without_network(self):
        record = artifact()
        calls = []
        def opener(request):
            calls.append(request)
            self.assertIsNone(request.get_header('Authorization'))
            return Reply(b'fixture bytes')
        self.assertEqual(m.transfer(self.root, record, opener), 'downloaded-and-verified')
        self.assertEqual(len(calls), 1)
        self.assertEqual(m.transfer(self.root, record, lambda _: self.fail('existing file redownloaded')), 'verified-existing')
        self.assertEqual((self.root / record['path']).read_bytes(), b'fixture bytes')

    def test_resume_requires_exact_range_and_preserves_partial_on_rejection(self):
        record = artifact(b'abcdef')
        part = self.root / (record['path'] + '.part')
        part.parent.mkdir()
        part.write_bytes(b'abc')
        with self.assertRaisesRegex(ValueError, 'RANGE_RESUME_NOT_CONFIRMED'):
            m.transfer(self.root, record, lambda _: Reply(b'abcdef'))
        self.assertEqual(part.read_bytes(), b'abc')
        def opener(request):
            self.assertEqual(request.get_header('Range'), 'bytes=3-')
            return Reply(b'def', 206, {'Content-Range': 'bytes 3-5/6'})
        m.transfer(self.root, record, opener)
        self.assertEqual((self.root / record['path']).read_bytes(), b'abcdef')

    def test_corrupt_download_is_not_promoted(self):
        record = artifact(b'expected')
        with self.assertRaisesRegex(ValueError, 'ARTIFACT_HASH_MISMATCH'):
            m.transfer(self.root, record, lambda _: Reply(b'incorrec'))
        self.assertFalse((self.root / record['path']).exists())
        self.assertEqual((self.root / (record['path'] + '.part')).read_bytes(), b'incorrec')

    def test_existing_or_symlink_destination_is_not_overwritten(self):
        keep = self.root / 'keep'
        keep.write_bytes(b'unchanged')
        with self.assertRaises(ValueError):
            m.new_destination(str(self.root))
        link = self.root / 'link'
        link.symlink_to(keep)
        with self.assertRaises(ValueError):
            m.checked_file(self.root, 'link')
        self.assertEqual(keep.read_bytes(), b'unchanged')

    def test_manifest_binding_prevents_cross_profile_resume(self):
        m.receipt(self.root, 'fixture', 'a' * 64, 'incomplete', [])
        m.bound_receipt(self.root, 'fixture', 'a' * 64)
        with self.assertRaisesRegex(ValueError, 'BUNDLE_MANIFEST_OR_PROFILE_MISMATCH'):
            m.bound_receipt(self.root, 'fixture', 'b' * 64)

    def test_synthetic_full_bundle_download_and_offline_verify(self):
        dest = self.root / 'new'
        value = {'files': [artifact()]}
        with patch.object(m, 'network', return_value=Reply(b'fixture bytes')), contextlib.redirect_stderr(io.StringIO()):
            result = m.download(str(dest), 'fixture', value, 'c' * 64)
        self.assertEqual(result['state'], 'complete-bundle-only')
        self.assertFalse(result['runtimeInstalled'])
        self.assertFalse(result['activated'])
        m.bound_receipt(dest, 'fixture', 'c' * 64)
        m.verify_file(m.checked_file(dest, value['files'][0]['path']), value['files'][0])


if __name__ == '__main__':
    unittest.main()
