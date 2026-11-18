from __future__ import annotations

import argparse
from pathlib import Path

import imageio.v3 as iio

from .alpha_models import BiRefNetMatting, SAM2VideoTracker
from .alpha_pipeline import AlphaConfig, run_alpha_pipeline


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract temporally stabilized RGBA frames from a video")
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--mode", choices=("subject", "effect-luma"), default="subject")
    parser.add_argument("--initial-mask", type=Path)
    parser.add_argument("--sam-model", default="facebook/sam2.1-hiera-small")
    parser.add_argument("--matting-model", default="ZhengPeng7/BiRefNet-matting")
    parser.add_argument("--temporal-strength", type=float, default=0.25)
    parser.add_argument("--trimap-radius", type=int, default=6)
    parser.add_argument("--luminance-threshold", type=float, default=0.04)
    parser.add_argument("--luminance-gamma", type=float, default=1.0)
    parser.add_argument("--max-frames", type=int, default=1200)
    args = parser.parse_args()
    config = AlphaConfig(args.mode, args.trimap_radius, args.temporal_strength, args.luminance_threshold, args.luminance_gamma, args.max_frames)
    kwargs = {}
    if args.mode == "subject":
        if args.initial_mask is None:
            parser.error("--initial-mask is required in subject mode")
        kwargs = {
            "initial_mask": iio.imread(args.initial_mask),
            "tracker": SAM2VideoTracker(args.sam_model),
            "matting": BiRefNetMatting(args.matting_model),
        }
    result = run_alpha_pipeline(args.source, args.output, config, **kwargs)
    print(result["directories"]["rgba"])


if __name__ == "__main__":
    main()
