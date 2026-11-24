"""Opt-in preparation of a scoped prop I2V source. Never removes background."""
import argparse
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path

from PIL import Image

MAX_INPUT_BYTES = 8 * 1024 * 1024
MAX_INPUT_PIXELS = 8_388_608
MAX_INPUT_DIMENSION = 4096
PRESET = 'candle-headroom-v1'
CANVAS = (512, 352)
KEY = (0, 255, 0)
FIT_PERCENT = 78
BOTTOM_MARGIN = 12


def prepare(source, preset):
    if preset != PRESET:
        raise ValueError('PROP_I2V_FRAMING_PRESET')
    if not isinstance(source, bytes) or not 57 <= len(source) <= MAX_INPUT_BYTES:
        raise ValueError('PROP_I2V_FRAMING_SOURCE_SIZE')

    with Image.open(BytesIO(source), formats=('PNG',)) as original:
        sw, sh = original.size
        if getattr(original, 'is_animated', False) or getattr(original, 'n_frames', 1) != 1:
            raise ValueError('PROP_I2V_FRAMING_STATIC_PNG_REQUIRED')
        if not (1 <= sw <= MAX_INPUT_DIMENSION and 1 <= sh <= MAX_INPUT_DIMENSION and sw * sh <= MAX_INPUT_PIXELS):
            raise ValueError('PROP_I2V_FRAMING_SOURCE_DIMENSIONS')
        cw, ch = CANVAS
        # Bound the preserved source rectangle to 78% of the canvas dimensions.
        # Resize the whole source (not an inferred subject mask); never crop or stretch.
        cap_w, cap_h = cw * FIT_PERCENT // 100, ch * FIT_PERCENT // 100
        if cap_w * sh <= cap_h * sw:
            width, height = cap_w, max(1, (cap_w * sh + sw // 2) // sw)
        else:
            width, height = max(1, (cap_h * sw + sh // 2) // sh), cap_h
        left, top = (cw - width) // 2, ch - BOTTOM_MARGIN - height
        # Composite at original resolution, then resize RGB. This matches the
        # validated two-step experiment and makes native RGBA input equivalent
        # to an already-green RGB still derived from the same alpha bytes.
        matte = Image.alpha_composite(Image.new('RGBA', (sw, sh), (*KEY, 255)), original.convert('RGBA')).convert('RGB')
        fitted = matte.resize((width, height), Image.Resampling.LANCZOS)
        canvas = Image.new('RGB', CANVAS, KEY)
        canvas.paste(fitted, (left, top))

    out = BytesIO()
    canvas.save(out, format='PNG')
    result = out.getvalue()
    proof = {
        'preset': preset,
        'sourceSha256': sha256(source).hexdigest(),
        'renderInputSha256': sha256(result).hexdigest(),
        'canvas': {'width': cw, 'height': ch, 'fill': '#00FF00'},
        'placedSource': {'x': left, 'y': top, 'width': width, 'height': height},
    }
    return result, proof


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preset', choices=[PRESET], required=True)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    with args.source.open('rb') as read:
        source = read.read(MAX_INPUT_BYTES + 1)
    result, proof = prepare(source, args.preset)
    with args.output.open('xb') as write:
        write.write(result)
    print(json.dumps(proof, separators=(',', ':')))


if __name__ == '__main__':
    main()
