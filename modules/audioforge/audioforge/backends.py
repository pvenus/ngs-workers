from __future__ import annotations
import math
import os
import struct
import subprocess
import time
import wave
from pathlib import Path
from typing import Callable

class Cancelled(Exception): pass

class MockBackend:
    """Contract backend producing a deterministic stereo WAV; not a generative model."""
    name = "mock"
    def generate(self, prompt: str, duration: float, seed: int, model: str, output: Path, cancelled: Callable[[], bool], settings=None):
        rate, frames = 44100, int(44100 * duration); frequency = 220 + seed % 220
        output.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(output), "wb") as wav:
            wav.setnchannels(2); wav.setsampwidth(2); wav.setframerate(rate)
            for index in range(frames):
                if index % 4096 == 0 and cancelled(): raise Cancelled()
                envelope = max(0.0, 1.0 - index / max(1, frames)); sample = int(10000 * envelope * math.sin(2 * math.pi * frequency * index / rate))
                wav.writeframesraw(struct.pack("<hh", sample, sample))
        return {"synthetic_test_artifact": True, "model": "mock-tone-contract", "model_version": "mock-v1", "license": "N/A - synthetic contract test"}

class StableAudio3CLIBackend:
    """Per-job adapter for the official Apple Silicon optimized/mlx CLI."""
    name = "stable-audio-3-mlx"
    def generate(self, prompt: str, duration: float, seed: int, model: str, output: Path, cancelled: Callable[[], bool], settings=None):
        from .config import STABLE_AUDIO_MLX_DIR
        profiles = {
            "small-sfx": ("sm-sfx", "small-sfx", "dit_sm-sfx_f16.npz", "7e702d2640699a57fe436ca975fda16832040ba568c1e092c2ae826987558118"),
            "sm-sfx": ("sm-sfx", "small-sfx", "dit_sm-sfx_f16.npz", "7e702d2640699a57fe436ca975fda16832040ba568c1e092c2ae826987558118"),
            "small-music": ("sm-music", "small-music", "dit_sm-music_f16.npz", "8ed3f38e2597f361ee675051f1265d9aa2ae2fffce1c61acd2e9fe31e1db1cbc"),
            "sm-music": ("sm-music", "small-music", "dit_sm-music_f16.npz", "8ed3f38e2597f361ee675051f1265d9aa2ae2fffce1c61acd2e9fe31e1db1cbc"),
        }
        if model not in profiles:
            raise ValueError(f"unsupported model profile: {model}")
        dit, logical_model, dit_filename, dit_sha256 = profiles[model]
        command = os.getenv("AUDIOFORGE_STABLE_AUDIO_COMMAND", str(STABLE_AUDIO_MLX_DIR / "sa3"))
        output.parent.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        process = subprocess.Popen([command,"--prompt",prompt,"--dit",dit,"--decoder","same-s","--seconds",str(duration),"--steps","8","--seed",str(seed),"--out",str(output),"--free-models"], cwd=STABLE_AUDIO_MLX_DIR)
        while process.poll() is None:
            if cancelled(): process.terminate(); process.wait(timeout=10); raise Cancelled()
            try: process.wait(timeout=.2)
            except subprocess.TimeoutExpired: pass
        if process.returncode: raise RuntimeError(f"stable-audio MLX exited with {process.returncode}")
        return {"synthetic_test_artifact": False, "provider": "stability-ai", "profile": logical_model, "model": logical_model, "model_version": "stable-audio-3", "runtime": "official optimized/mlx", "runtime_commit": os.getenv("AUDIOFORGE_RUNTIME_COMMIT", "779434a908193105335fd8d833418603625b2859"), "generation_seconds": round(time.monotonic() - started, 3), "weights_repo": "stabilityai/stable-audio-3-optimized", "weights_snapshot": "da6edc54ddba10bfd79a077102ded687f80e882b", "weight_sha256": {dit_filename: dit_sha256, "same_s_decoder_f32.npz": "909928a8e6937c1ebe6ac4b729f0462bd3773704a11ea18278e42671dc69bfe4", "same_s_encoder_f32.npz": "a48f80d81c30d74c45e2a3047082c4891f715e24c44645adb9c1f4f07afdaf0c", "t5gemma_f16.npz": "8deb20489f36d9aec539f26c9c67321f99bc5fe300d470435ed6e76be4f16bbd"}}

def make_backend(name: str):
    if name == "mock": return MockBackend()
    if name in {"stable-audio-3", "small-sfx"}: return StableAudio3CLIBackend()
    if name in {"ace-step", "ace-step-2b", "acestep-v15-turbo", "ace-step-1.5-turbo"}:
        from .acestep_backend import AceStepBackend
        return AceStepBackend()
    raise ValueError(f"unknown backend: {name}")
