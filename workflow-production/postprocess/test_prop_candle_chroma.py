from hashlib import sha256
from io import BytesIO
from pathlib import Path
import runpy
import tempfile
import unittest

from PIL import Image

program = runpy.run_path(str(Path(__file__).with_name('prop-derive.py')))
key = program['connected_candle_chroma']
process = program['process']
PROFILE = 'candle-chroma-headroom-v1'
SIZE = (512,352)
GREEN = (0,255,0)


def candle(frame):
    im = Image.new('RGB',SIZE,GREEN)
    im.paste((255,100+frame,8),(188,21,240,120))   # warm flame
    im.paste((72,38,17),(101,120,405,331))       # opaque holder
    im.paste(GREEN,(113,154,124,165))           # hole in foreground, not edge connected
    im.putpixel((1,6),(80,160,19))              # soft transparent green at edge
    im.putpixel((511,19),(255,100,8))            # never remove warm RGB even at edge
    return im


class PropCandleChromaTests(unittest.TestCase):
    def test_only_edge_connected_green_changes(self):
        rgba = key(candle(0))
        self.assertEqual(rgba.getpixel((15,15)),(0,0,0,0))
        self.assertEqual(rgba.getpixel((115,158)),(*GREEN,255))
        self.assertEqual(rgba.getpixel((511,19)),(255,100,8,255))
        self.assertEqual(rgba.getpixel((200,92)),(255,100,8,255))
        self.assertEqual(rgba.getpixel((200,180)),(72,38,17,255))
        edge=rgba.getpixel((1,6))
        self.assertTrue(0 < edge[3] < 255)
        self.assertLess(edge[1],160)
        for rgb in [(0,0,0),GREEN]:
            with self.assertRaisesRegex(ValueError,'CHROMA_FRAME'):
                key(Image.new('RGB',SIZE,rgb))

    def test_detail_passthrough_timing_transparency_and_matte_exclusion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'raw').mkdir()
            frames=[]
            for n in range(3):
                png=BytesIO();candle(n).save(png,format='PNG');wire=png.getvalue()
                frames.append({'sha256':sha256(wire).hexdigest(),'bytes':len(wire),'width':512,'height':352})
                (root/'raw'/f'frame-{n:08d}.png').write_bytes(wire)
            spec={'kind':'detail','canvas':{'width':512,'height':352},
                  'frameDurationsMs':[42,41,42],'sourceHash':'a'*64,'frames':frames,'renderProfile':PROFILE}
            def forbidden_matte(*args):
                raise AssertionError('profile must never invoke learned matte')
            wire=process(root,spec,forbidden_matte)
            with Image.open(BytesIO(wire)) as im:
                self.assertEqual(im.n_frames,3)
                for n in range(3):
                    im.seek(n)
                    self.assertEqual(im.info['duration'],spec['frameDurationsMs'][n])
                    self.assertEqual(im.getpixel((200,92)),(255,100+n,8,255))
                    self.assertEqual(im.getpixel((15,15)),(0,0,0,0))
            for invalid in [{**spec,'renderProfile':'unknown'},{**spec,'frameDurationsMs':[]},{**spec,'kind':'pixel'}]:
                with self.assertRaisesRegex(ValueError,'RENDER_PROFILE'):
                    process(root,invalid,forbidden_matte)


if __name__=='__main__':
    unittest.main()
