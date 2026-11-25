"""Verified PNG inputs/outputs for the local CorridorKey MLX 2.0 prop stage."""
import hashlib
import io
import json
from pathlib import Path
import sys

from PIL import Image

ERROR = 'PROP_DERIVATION_CORRIDOR_OUTPUT'


def require(ok):
    if not ok:
        raise ValueError(ERROR)


def layout(spec):
    rows, times = spec['frames'], spec['frameDurationsMs']
    require(spec['kind'] == 'detail' and len(rows) == (len(times) or 1))
    width, height = rows[0]['width'], rows[0]['height']
    if times:
        require(len(times) >= 2 and (width, height) == (512, 352))
        pw, ph = 1280, 768
    else:
        pw, ph = 1536, 1024
        require(12 <= width <= pw and 12 <= height <= ph)
    require(all((row['width'], row['height']) == (width, height) for row in rows))
    return width,height,pw,ph,(pw-width)//2,(ph-height)//2


def prepare(root):
    import numpy as np
    spec = json.loads((root / 'job.json').read_bytes())
    width,height,pw,ph,left,top = layout(spec)
    dest = root / 'corridor-input'
    dest.mkdir(mode=0o700)
    for index, row in enumerate(spec['frames']):
        source = (root / 'raw' / f'frame-{index:08d}.png').read_bytes()
        require(len(source) == row['bytes'] and hashlib.sha256(source).hexdigest() == row['sha256'])
        with Image.open(io.BytesIO(source)) as image:
            require(image.format == 'PNG' and image.mode == 'RGB'
                    and image.size == (width,height) and image.n_frames == 1)
            # No resampling; extend each edge so there is no green shade jump.
            pad = ((top,ph-height-top),(left,pw-width-left),(0,0))
            pixels = np.pad(np.asarray(image), pad, mode='edge')
            Image.fromarray(pixels).save(dest / f'{index:08d}.png')


def cutouts(root, spec):
    import cv2
    import numpy as np
    width,height,pw,ph,left,top = layout(spec)
    folder = root / 'corridor' / 'cutout'
    paths = [folder / f'{index:06d}.png' for index in range(len(spec['frames']))]
    require(set(folder.iterdir()) == set(paths))
    result = []
    for path in paths:
        require(path.is_file() and not path.is_symlink())
        frame = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        require(frame is not None and frame.dtype == np.uint16 and frame.shape == (ph,pw,4))
        crop = frame[top:top+height,left:left+width,[2,1,0,3]]
        rgba = ((crop.astype(np.uint32) + 128) // 257).astype(np.uint8)
        alpha = rgba[:,:,3]
        require(np.count_nonzero(alpha == 0) >= alpha.size // 100 and
                np.count_nonzero(alpha >= 250) >= alpha.size // 100)
        if not spec['frameDurationsMs']:
            require(all(alpha[y,x] <= 2 for x,y in ((0,0),(0,height-1),(width-1,0),(width-1,height-1))))
        result.append(Image.fromarray(rgba))
    return result


if __name__ == '__main__':
    try:
        prepare(Path(sys.argv[1]))
    except Exception:
        sys.exit(43)
