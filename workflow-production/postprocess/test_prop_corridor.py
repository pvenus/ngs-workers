from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest

import cv2
import numpy as np
from PIL import Image

from prop_corridor import prepare, cutouts
from test_prop_candle_chroma import process, candle, PROFILE


LEFT, TOP = 384, 208


class PropCorridor(unittest.TestCase):
    def test_preserves_timing_geometry_and_fractional_alpha_without_regeneration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'raw').mkdir()
            spec = {'kind': 'detail', 'canvas': {'width': 512, 'height': 352},
                    'frameDurationsMs': [80, 90], 'frames': [], 'renderProfile': PROFILE}
            (root / 'corridor' / 'cutout').mkdir(parents=True)
            for i in range(2):
                raw = root / 'raw' / f'frame-{i:08d}.png'
                candle(i).save(raw)
                data = raw.read_bytes()
                spec['frames'].append({'bytes': len(data), 'sha256': sha256(data).hexdigest(),
                                       'width': 512, 'height': 352})
                out = np.zeros((768, 1280, 4), dtype=np.uint16)
                out[TOP + 10:TOP + 100, LEFT + 30:LEFT + 140] = (0, 2570, 65535, 65535)
                out[TOP + 11, LEFT + 31, 3] = 128 * 257
                self.assertTrue(cv2.imwrite(str(root / 'corridor' / 'cutout' / f'{i:06d}.png'), out))
            (root / 'job.json').write_text(json.dumps(spec))
            prepare(root)
            with Image.open(root / 'corridor-input' / '00000001.png') as padded:
                self.assertEqual(padded.size, (1280, 768))
                self.assertEqual(padded.crop((LEFT, TOP, LEFT+512, TOP+352)).tobytes(), candle(1).tobytes())
            def forbidden(*args):
                raise AssertionError('ordinary matting must not run')
            result = process(root, spec, forbidden, corridor=True)
            with Image.open(BytesIO(result)) as apng:
                self.assertEqual(apng.n_frames, 2)
                self.assertEqual(apng.size, (512, 352))
                for i, delay in enumerate(spec['frameDurationsMs']):
                    apng.seek(i)
                    self.assertEqual(apng.info['duration'], delay)
                    self.assertEqual(apng.getpixel((31, 11)), (255, 10, 0, 128))
                    self.assertEqual(apng.getpixel((1, 1)), (0, 0, 0, 0))
            (root / 'corridor' / 'cutout' / '000001.png').unlink()
            with self.assertRaisesRegex(ValueError, 'CORRIDOR_OUTPUT'):
                cutouts(root, spec)
