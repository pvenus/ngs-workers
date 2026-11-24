from hashlib import sha256
import importlib.util
from io import BytesIO
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from PIL import Image, ImageDraw

MODULE = Path(__file__).with_name('prop-i2v-input-framing.py')
SPEC = importlib.util.spec_from_file_location('prop_i2v_input_framing', MODULE)
framing = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(framing)


def png(img, **opts):
    buffer = BytesIO()
    img.save(buffer, format='PNG', **opts)
    return buffer.getvalue()


class TestPropI2VInputFraming(unittest.TestCase):
    def test_full_rgb_source_keeps_all_edges_and_aspect_with_extra_top_room(self):
        src = Image.new('RGB', (1536, 1024), (50, 30, 10))
        draw = ImageDraw.Draw(src)
        draw.rectangle((0, 0, 300, 240), fill='red')
        draw.rectangle((1235, 780, 1535, 1023), fill='blue')
        original = png(src)
        out, proof = framing.prepare(original, 'candle-headroom-v1')
        self.assertEqual(proof['placedSource'], {'x': 56, 'y': 74, 'width': 399, 'height': 266})
        self.assertEqual(proof['sourceSha256'], sha256(original).hexdigest())
        self.assertEqual(proof['renderInputSha256'], sha256(out).hexdigest())
        with Image.open(BytesIO(out)) as img:
            self.assertEqual((img.mode, img.size), ('RGB', (512, 352)))
            self.assertEqual(img.getpixel((0, 0)), (0, 255, 0))
            self.assertEqual(img.getpixel((260, 73)), (0, 255, 0))
            self.assertEqual(img.getpixel((260, 340)), (0, 255, 0))
            self.assertEqual(img.getpixel((58, 76)), (255, 0, 0))
            self.assertEqual(img.getpixel((451, 336)), (0, 0, 255))

    def test_only_existing_alpha_reveals_green_opaque_black_is_not_keyed(self):
        src = Image.new('RGBA', (512, 352), (0, 0, 0, 0))
        ImageDraw.Draw(src).rectangle((90, 70, 420, 320), fill=(0, 0, 0, 255))
        out, info = framing.prepare(png(src), 'candle-headroom-v1')
        # Exact same prepared model input from the unmodified native still or
        # its previous full-scale green composite (including soft edges).
        green = Image.alpha_composite(Image.new('RGBA',src.size,(0,255,0,255)),src).convert('RGB')
        self.assertEqual(out,framing.prepare(png(green),'candle-headroom-v1')[0])
        with Image.open(BytesIO(out)) as img:
            self.assertEqual(img.getpixel((240, 160)), (0, 0, 0))
            self.assertEqual(img.getpixel((260, info['placedSource']['y'] + 10)), (0, 255, 0))

    def test_vertical_sources_are_contained_and_accidental_parameters_are_rejected(self):
        src = Image.new('RGB', (512, 1024), 'orange')
        out, info = framing.prepare(png(src), 'candle-headroom-v1')
        self.assertEqual(info['placedSource'], {'x': 187, 'y': 66, 'width': 137, 'height': 274})
        self.assertRaisesRegex(ValueError, 'FRAMING_PRESET', framing.prepare, png(src), 'cinematic')
        self.assertRaisesRegex(ValueError, 'STATIC_PNG_REQUIRED', framing.prepare,
            png(src, save_all=True, append_images=[Image.new('RGB', src.size, 'blue')]), 'candle-headroom-v1')
        self.assertRaisesRegex(ValueError, 'FRAMING_SOURCE_SIZE', framing.prepare,
            b'a' * (framing.MAX_INPUT_BYTES + 1), 'candle-headroom-v1')
        self.assertRaisesRegex(ValueError, 'FRAMING_SOURCE_DIMENSIONS', framing.prepare,
            png(Image.new('RGB', (4097, 1))), 'candle-headroom-v1')

    def test_cli_no_clobber_and_source_kept_readonly(self):
        with tempfile.TemporaryDirectory() as directory:
            src = Path(directory) / 'source.png'
            dest = Path(directory) / 'framed.png'
            source = png(Image.new('RGB', (8, 8), 'purple'))
            src.write_bytes(source)
            cmd = [sys.executable, '-B', str(MODULE), '--preset', 'candle-headroom-v1',
                   '--source', str(src), '--output', str(dest)]
            subprocess.run(cmd, check=True, capture_output=True)
            initial = dest.read_bytes()
            self.assertNotEqual(subprocess.run(cmd, check=False, capture_output=True).returncode, 0)
            self.assertEqual(src.read_bytes(), source)
            self.assertEqual(dest.read_bytes(), initial)


if __name__ == '__main__':
    unittest.main()
