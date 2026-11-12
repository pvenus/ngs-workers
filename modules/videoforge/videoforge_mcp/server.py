from __future__ import annotations
import subprocess
import sys
import uuid
from pathlib import Path
from datetime import datetime, timezone
from typing import Any
from mcp.server.fastmcp import FastMCP
from .config import JOB_DIR, MODEL_DIR, MODEL_ID, OUTPUT_DIR
from .jobs import read_job, write_job
from .queue import JobQueue
from .frames import extract_candidates
from .contracts import selection_manifest
import os

mcp = FastMCP("VideoForge")

@mcp.tool(structured_output=True)
def generate_video(prompt: str, width: int = 256, height: int = 144, frames: int = 8, steps: int = 8, fps: int = 8, seed: int = 42) -> dict[str, Any]:
    """Queue a local text-to-video job and immediately return its job id."""
    prompt = prompt.strip()
    if not prompt:
        raise ValueError("prompt is required")
    if width % 8 or height % 8 or not (64 <= width <= 576) or not (64 <= height <= 576):
        raise ValueError("width and height must be multiples of 8 between 64 and 576")
    if not (2 <= frames <= 24 and 1 <= steps <= 40 and 1 <= fps <= 30):
        raise ValueError("frames=2..24, steps=1..40, fps=1..30")
    job_id = uuid.uuid4().hex
    job = {"job_id": job_id, "status": "queued", "prompt": prompt, "width": width, "height": height, "frames": frames, "steps": steps, "fps": fps, "seed": seed, "model_id": MODEL_ID, "created_at": datetime.now(timezone.utc).isoformat()}
    write_job(job_id, job)
    subprocess.Popen([sys.executable, "-m", "videoforge_mcp.worker", job_id], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return {"ok": True, "job_id": job_id, "status": "queued"}

@mcp.tool(structured_output=True)
def get_video_job(job_id: str) -> dict[str, Any]:
    """Return status, output MP4 path, elapsed time, or generation error."""
    try:
        return read_job(job_id)
    except FileNotFoundError as exc:
        raise ValueError(f"unknown job id: {job_id}") from exc

@mcp.tool(structured_output=True)
def get_video_configuration() -> dict[str, Any]:
    """Return the relocatable model, output, and job directories."""
    return {"model_id": MODEL_ID, "model_dir": str(MODEL_DIR), "output_dir": str(OUTPUT_DIR), "job_dir": str(JOB_DIR), "capabilities": ["text-to-video"], "image_to_video": False}

@mcp.tool(structured_output=True)
def submit_portrait_video(request: dict[str, Any]) -> dict[str, Any]:
    """Validate rights and queue one profile-bound image-to-video job."""
    return JobQueue(JOB_DIR,Path(os.environ["VIDEOFORGE_INPUT_ROOT"])).submit(request)

@mcp.tool(structured_output=True)
def cancel_video_job(job_id: str) -> dict[str, Any]:
    return JobQueue(JOB_DIR).cancel(job_id)

@mcp.tool(structured_output=True)
def extract_video_candidates(source_path: str, output_dir: str, target_n: int = 30) -> dict[str, Any]:
    return extract_candidates(Path(source_path), Path(output_dir), target_n,Path(os.environ["VIDEOFORGE_OUTPUT_DIR"]),Path(os.environ["VIDEOFORGE_OUTPUT_DIR"]))

@mcp.tool(structured_output=True)
def save_video_selection(candidate_manifest_path: str, selected_candidate_ids: list[str], output_path: str) -> dict[str, Any]:
    return selection_manifest(Path(candidate_manifest_path), selected_candidate_ids, Path(output_path),Path(os.environ["VIDEOFORGE_OUTPUT_DIR"]),Path(os.environ["VIDEOFORGE_OUTPUT_DIR"]))

def main() -> None:
    mcp.run(transport="stdio")
