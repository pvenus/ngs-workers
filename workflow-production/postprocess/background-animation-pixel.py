"""Correct a verified upscaled background loop using its pinned pixel still's palette."""
from pathlib import Path
import sys
from PIL import Image



def open_rgb(p):
    with Image.open(p) as image:
        if image.format != 'PNG' or image.mode != 'RGB' or getattr(image, 'n_frames', 1) != 1:
            raise ValueError('BACKGROUND_ANIMATION_PIXEL_EXPECTED_RGB')
        return image.copy()


def correct(root, width, height):
    if not all(256 <= n <= 1024 and n % 64 == 0 for n in (width, height)):
        raise ValueError('BACKGROUND_ANIMATION_PIXEL_GEOMETRY')
    reference = open_rgb(root / 'reference.png')
    if reference.size != (1536, 1024):
        raise ValueError('BACKGROUND_ANIMATION_PIXEL_REFERENCE_GEOMETRY')
    histogram = reference.getcolors(33)
    if histogram is None or len(histogram) > 32:
        raise ValueError('BACKGROUND_ANIMATION_PIXEL_REFERENCE_PALETTE')
    colors = sorted(color for _, color in histogram)
    # Use one fixed palette for the whole sequence; do not create new color
    # ramps per frame, dither, change coordinates, or shrink the canvas.
    palette = Image.new('P', (1, 1))
    palette.putpalette([channel for color in colors + [colors[0]] * (256-len(colors)) for channel in color])
    for i in range(45):
        name = f'{i:04}.png'
        image = open_rgb(root / 'input' / name)
        if image.size != (width, height):
            raise ValueError('BACKGROUND_ANIMATION_PIXEL_FRAME_GEOMETRY')
        image.quantize(palette=palette, dither=Image.Dither.NONE).convert('RGB').save(root / 'output' / name, format='PNG')


if __name__ == '__main__':
    correct(Path(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3]))
