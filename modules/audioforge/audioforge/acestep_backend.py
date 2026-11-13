"""Lazy, bounded ACE-Step subprocess adapter; no model imports in the service."""
from __future__ import annotations
import fcntl
import hashlib
import json
import os
import shutil
import signal
import subprocess
import time
import wave
from pathlib import Path
from .backends import Cancelled


class AceStepBackend:
    """Run the existing local Turbo installation with default OS memory protection."""
    name = 'ace-step-mlx'

    def __init__(self, runtime_root=None, artifact_root=None, timeout=900):
        from .config import ARTIFACT_DIR
        self.root = Path(runtime_root or os.getenv('AUDIOFORGE_ACESTEP_ROOT', str(Path(__file__).resolve().parents[1] / 'runtime/acestep'))).resolve()
        self.artifacts = Path(artifact_root or ARTIFACT_DIR).resolve()
        # A 30-second, 8-step MLX generation on lower-memory Apple Silicon can
        # legitimately take more than 15 minutes. Keep the subprocess bounded,
        # while matching the runtime's configured 20-minute generation limit.
        self.timeout = min(float(timeout), 1200)
        self.runner = Path(__file__).with_name('acestep_runner.py')
        self.python = self.root / 'source/.venv/bin/python'

    def _environment(self):
        env = os.environ.copy()
        for key in ('PYTORCH_MPS_HIGH_WATERMARK_RATIO', 'PYTORCH_MPS_LOW_WATERMARK_RATIO'):
            env.pop(key, None)
        env.update(HF_HOME=str(self.root / 'cache/huggingface'), HF_HUB_OFFLINE='1',
            TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
            HF_HUB_DISABLE_IMPLICIT_TOKEN='1', GRADIO_ANALYTICS_ENABLED='False',
            DO_NOT_TRACK='1', ACESTEP_CHECKPOINTS_DIR=str(self.root / 'source/checkpoints'),
            PYTORCH_ENABLE_MPS_FALLBACK='1', TOKENIZERS_PARALLELISM='false',
            MPLCONFIGDIR=str(self.root / 'cache/matplotlib'))
        return env

    @staticmethod
    def _stop(process):
        if process.poll() is not None:
            return
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()

    def generate(self, prompt, duration, seed, model, output, cancelled, settings=None):
        """Forward the author's prompt unchanged, returning measured WAV provenance."""
        if model not in ('ace-step-2b', 'acestep-v15-turbo', 'ace-step-1.5-turbo'):
            raise ValueError('unsupported ACE profile')
        settings = dict(settings or {})
        fixed = {'inference_steps': 8, 'guidance_scale': 1.0, 'normalization_db': -1.0}
        if set(settings) - {'bpm', *fixed}:
            raise ValueError('unsupported ACE settings')
        for key, value in fixed.items():
            if key in settings and settings[key] != value:
                raise ValueError(f'{key} must be {value}')
        bpm = settings.get('bpm')
        if bpm is not None and (type(bpm) is not int or not 30 <= bpm <= 300):
            raise ValueError('bpm must be an integer from 30 to 300')
        if not isinstance(prompt, str) or not prompt.strip() or not 0.25 <= duration <= 30:
            raise ValueError('invalid prompt or duration')
        output = Path(output)
        if output.is_symlink() or output.parent.is_symlink():
            raise ValueError('symlink output is forbidden')
        output = output.resolve()
        if not output.is_relative_to(self.artifacts) or output.suffix != '.wav':
            raise ValueError('output must be a WAV under artifacts')
        if output.exists():
            raise FileExistsError(output)
        if cancelled():
            raise Cancelled()
        output.parent.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(self.root).free < 10 * 1024**3:
            raise RuntimeError('less than 10GiB free disk')
        request = dict(prompt=prompt, duration=duration, seed=seed, model=model,
                       settings=settings, output=str(output), runtime_root=str(self.root), parent_pid=os.getpid())
        request_path = output.parent / 'ace-request.json'
        request_path.write_text(json.dumps(request, ensure_ascii=False, indent=2))
        self.root.joinpath('cache').mkdir(exist_ok=True)
        started = time.monotonic()
        with (self.root / 'cache/gateway-generation.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError('another ACE generation is running') from exc
            with (output.parent / 'ace-runtime.log').open('wb') as log:
                process = subprocess.Popen([str(self.python), str(self.runner), str(request_path)],
                    cwd=self.root / 'source', env=self._environment(), stdin=subprocess.DEVNULL,
                    stdout=log, stderr=log, start_new_session=True, pass_fds=(lock.fileno(),))
                try:
                    while process.poll() is None:
                        if cancelled():
                            raise Cancelled()
                        if time.monotonic() - started >= self.timeout:
                            raise TimeoutError('ACE generation exceeded time limit')
                        if shutil.disk_usage(self.root).free < 10 * 1024**3:
                            raise RuntimeError('less than 10GiB free disk')
                        time.sleep(.2)
                    if process.returncode:
                        raise RuntimeError(f'ACE exited with {process.returncode}; see ace-runtime.log')
                    if cancelled():
                        raise Cancelled()
                except BaseException:
                    self._stop(process)
                    output.unlink(missing_ok=True)
                    raise
        with wave.open(str(output), 'rb') as wav:
            rate, channels = wav.getframerate(), wav.getnchannels()
            actual_duration = wav.getnframes() / rate
            if wav.getsampwidth() != 2 or channels != 2 or abs(actual_duration-duration) > .1:
                output.unlink(missing_ok=True)
                raise ValueError('unexpected ACE WAV format or duration')
        return {'synthetic_test_artifact': False, 'provider': 'ace-step',
            'model': 'acestep-v15-turbo', 'model_version': 'ACE-Step-1.5', 'license': 'MIT',
            'runtime': 'native MLX', 'sample_rate': rate, 'channels': channels,
            'duration_seconds': actual_duration, 'generation_seconds': time.monotonic()-started,
            'runtime_commit': 'ca1e85fe9430179831e6bc6be790c332190a3866',
            'weights_snapshot': '19671f406d603126926c1b7e2adc169acbcade22',
            'settings': {**fixed, **settings}, 'allocator_limits': 'OS defaults',
            'audio_sha256': hashlib.sha256(output.read_bytes()).hexdigest()}
