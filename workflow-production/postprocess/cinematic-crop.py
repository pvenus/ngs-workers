"""Apply a provided, half-open rectangle. No aspect inference, resize, or generation."""
import sys
from PIL import Image

_, source, target, *coords = sys.argv
w, h, x0, y0, x1, y1 = map(int, coords)
if not (0 <= x0 < x1 <= w <= 4096 and 0 <= y0 < y1 <= h <= 4096 and w*h <= 8388608):
    raise ValueError("CINEMATIC_CROP_OUT_OF_BOUNDS")
with Image.open(source, formats=["PNG"]) as image:
    if image.size != (w,h) or getattr(image,"n_frames",1) != 1 or image.mode not in ("RGB","RGBA"):
        raise ValueError("CINEMATIC_CROP_SOURCE_PNG")
    cropped = image.crop((x0,y0,x1,y1))
    profile = image.info.get("icc_profile")
    cropped.save(target, format="PNG", **({"icc_profile":profile} if profile else {}))
