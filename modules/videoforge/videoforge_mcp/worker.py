from __future__ import annotations
import argparse
import time
import traceback
from datetime import datetime, timezone
from .config import MODEL_DIR, OUTPUT_DIR
from .jobs import read_job, write_job

def run(job_id: str) -> None:
    job = read_job(job_id)
    started = time.monotonic()
    job.update(status="running", started_at=datetime.now(timezone.utc).isoformat())
    write_job(job_id, job)
    try:
        import imageio.v3 as iio
        import numpy as np
        import torch
        from diffusers import DPMSolverMultistepScheduler, DiffusionPipeline
        if not MODEL_DIR.is_dir():
            raise FileNotFoundError(f"model not installed: {MODEL_DIR}")
        pipe = DiffusionPipeline.from_pretrained(
            str(MODEL_DIR), torch_dtype=torch.float16, variant="fp16",
            use_safetensors=True, local_files_only=True,
        )
        pipe.scheduler = DPMSolverMultistepScheduler.from_config(pipe.scheduler.config)
        if hasattr(pipe, "enable_attention_slicing"):
            pipe.enable_attention_slicing()
        if hasattr(pipe, "enable_vae_slicing"):
            pipe.enable_vae_slicing()
        device = "mps" if torch.backends.mps.is_available() else "cpu"
        pipe.to(device)
        generator = torch.Generator(device="cpu").manual_seed(job["seed"])
        result = pipe(job["prompt"], width=job["width"], height=job["height"], num_frames=job["frames"], num_inference_steps=job["steps"], generator=generator)
        frames = result.frames
        if isinstance(frames, np.ndarray) and frames.ndim == 5:
            frames = frames[0]
        elif isinstance(frames, list) and frames and isinstance(frames[0], list):
            frames = frames[0]
        frames = np.asarray(frames)
        if np.issubdtype(frames.dtype, np.floating):
            frames = np.clip(frames * 255.0, 0, 255).astype(np.uint8)
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        output = OUTPUT_DIR / f"{job_id}.mp4"
        iio.imwrite(
            output, frames, fps=job["fps"], codec="libx264",
            pixelformat="yuv420p", macro_block_size=1,
        )
        job.update(status="completed", output_path=str(output), elapsed_seconds=round(time.monotonic() - started, 3), completed_at=datetime.now(timezone.utc).isoformat())
    except Exception as exc:
        job.update(status="failed", error=f"{type(exc).__name__}: {exc}", traceback=traceback.format_exc(limit=8), elapsed_seconds=round(time.monotonic() - started, 3))
    write_job(job_id, job)

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("job_id")
    run(parser.parse_args().job_id)

if __name__ == "__main__":
    main()
