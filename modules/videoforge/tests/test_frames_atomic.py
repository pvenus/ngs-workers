import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from videoforge_mcp.frames import extract_candidates


class AtomicExtractionTests(unittest.TestCase):
    def test_failure_after_first_png_is_clean_and_retry_is_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source.mp4'
            source.write_bytes(b'fixture')
            output = root / 'candidates'
            frames = [np.zeros((2, 2, 3), dtype=np.uint8) for _ in range(4)]
            def broken(frames):
                yield frames[0]
                raise RuntimeError('decode_failed')
            with patch('videoforge_mcp.frames.iio.immeta', return_value={'fps': 2, 'nframes': 4}), \
                 patch('videoforge_mcp.frames.iio.imiter', side_effect=lambda _: broken(frames)):
                with self.assertRaisesRegex(RuntimeError, 'decode_failed'):
                    extract_candidates(source, output, 2, root, root)
            self.assertFalse(output.exists())
            self.assertEqual([], list(root.glob('.candidates.extract-*')))
            with patch('videoforge_mcp.frames.iio.immeta', return_value={'fps': 2, 'nframes': 4}), \
                 patch('videoforge_mcp.frames.iio.imiter', return_value=iter(frames)):
                result = extract_candidates(source, output, 2, root, root)
            self.assertEqual(4, len(result['candidates']))
            self.assertEqual(result, json.loads((output / 'candidates.json').read_text()))
            before = {p.name: p.read_bytes() for p in output.iterdir()}
            with self.assertRaisesRegex(FileExistsError, 'candidate_output_exists'):
                extract_candidates(source, output, 2, root, root)
            self.assertEqual(before, {p.name: p.read_bytes() for p in output.iterdir()})

    def test_cancel_and_restart_remove_incomplete_stage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source.mp4'
            source.write_bytes(b'fixture')
            output = root / 'candidates'
            frame = np.zeros((2, 2, 3), dtype=np.uint8)
            def cancelled(_):
                yield frame
                raise KeyboardInterrupt()
            with patch('videoforge_mcp.frames.iio.immeta', return_value={'fps': 2, 'nframes': 2}), \
                 patch('videoforge_mcp.frames.iio.imiter', side_effect=cancelled):
                with self.assertRaises(KeyboardInterrupt):
                    extract_candidates(source, output, 1, root, root)
            self.assertFalse(output.exists())
            self.assertEqual([], list(root.glob('.candidates.extract-*')))
            stale = root / '.candidates.extract-stale'
            stale.mkdir()
            (stale / 'frame-000000.png').write_bytes(b'incomplete')
            with patch('videoforge_mcp.frames.iio.immeta', return_value={'fps': 2, 'nframes': 2}), \
                 patch('videoforge_mcp.frames.iio.imiter', return_value=iter([frame, frame])):
                extract_candidates(source, output, 1, root, root)
            self.assertFalse(stale.exists())
            self.assertEqual(2, len(list(output.glob('*.png'))))


if __name__ == '__main__':
    unittest.main()
