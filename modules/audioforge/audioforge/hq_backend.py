"""Explicit LM4B -> process exit -> XL-SFT adapter; never imports model code.

Not activated by installation. HQ requests opt in to a versioned contract and
the owner must separately enable the service or supply the pinned NGS binding.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import time
import wave

from .backends import Cancelled
from .hq_contract import (MODEL, PROFILE_VERSION, RUNTIME_REVISION, LM_REVISION,
                          XL_REVISION, canonical, enabled, validate_call)

_VERIFIED = {}


def _sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def _canonical(path, exists=True):
    p = Path(path)
    if not p.is_absolute() or p.resolve(strict=exists) != p:
        raise ValueError('HQ_NONCANONICAL_PATH')
    return p


def _stamp(p):
    s = p.stat()
    if not stat.S_ISREG(s.st_mode):
        raise ValueError('HQ_REGULAR_FILE_REQUIRED')
    return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)


def _read_json(path):
    p = _canonical(path)
    if p.stat().st_size > 1024 * 1024:
        raise ValueError('HQ_RECEIPT_TOO_LARGE')
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise ValueError('HQ_DUPLICATE_JSON_KEY')
            out[key] = value
        return out
    value = json.loads(p.read_bytes(), object_pairs_hook=pairs)
    if not isinstance(value, dict):
        raise ValueError('HQ_RECEIPT_OBJECT_REQUIRED')
    canonical(value)
    return value


def _write_new(path, value):
    with Path(path).open('xb') as stream:
        stream.write((canonical(value) + '\n').encode())
        stream.flush(); os.fsync(stream.fileno())


class HQBackend:
    name = 'ace-xl-sft-lm4b-sequential'

    def __init__(self, *, runtime_root, artifact_root, runner_path=None,
                 python_path=None, timeout=1100, enabled=None, lock_fds=(),
                 lock_validator=None):
        self.root = _canonical(runtime_root)
        self.artifacts = _canonical(artifact_root, exists=False)
        self.runner = _canonical(runner_path or Path(__file__).with_name('ace_hq_runner.py'))
        self.python = Path(python_path or self.root / 'source/.venv/bin/python')
        if self.python != self.root / 'source/.venv/bin/python' or not self.python.is_file():
            raise ValueError('HQ_VENV_INTERPRETER_REQUIRED')
        self.timeout = min(float(timeout), 1200)
        if self.timeout <= 0:
            raise ValueError('HQ_TIMEOUT_INVALID')
        self.activation = enabled
        self.lock_fds = tuple(lock_fds)
        self.lock_validator = lock_validator
        if self.lock_fds and not callable(lock_validator):
            raise ValueError('HQ_INHERITED_LOCK_VALIDATOR_REQUIRED')

    @staticmethod
    def verify_runtime(runtime_root, deep=False):
        """Basic readiness reads metadata only; execution additionally hashes bytes.

        Deep verification is cached only while each inode/size/mtime/ctime and
        the expected manifest remain identical. It is not an inference test.
        """
        root = _canonical(runtime_root)
        source = root / 'source'
        pins_path = Path(__file__).with_name('hq_pins.json')
        pins = _read_json(pins_path)
        if (pins.get('schemaVersion') != 'ace-hq-runtime-pins.v1' or
                pins.get('runtimeRevision') != RUNTIME_REVISION or
                pins.get('modelRevision') != XL_REVISION or
                pins.get('plannerRevision') != LM_REVISION):
            raise ValueError('HQ_RUNTIME_MANIFEST_INVALID')
        def git(*args):
            return subprocess.check_output(['/usr/bin/git', '-C', str(source), *args],
                stderr=subprocess.DEVNULL, timeout=10, text=True).strip()
        if git('rev-parse', 'HEAD') != RUNTIME_REVISION:
            raise ValueError('HQ_RUNTIME_REVISION_MISMATCH')
        if git('status', '--porcelain', '--untracked-files=no'):
            raise ValueError('HQ_RUNTIME_TRACKED_MODIFICATIONS')
        if not (source / '.venv/bin/python').is_file():
            raise ValueError('HQ_RUNTIME_NOT_INSTALLED')
        rows = pins.get('files')
        if not isinstance(rows, list) or not rows:
            raise ValueError('HQ_RUNTIME_FILES_REQUIRED')
        seen = set()
        for item in rows:
            rel = item['path']
            if not isinstance(rel, str) or not rel.startswith('source/checkpoints/') or '..' in Path(rel).parts or rel in seen:
                raise ValueError('HQ_RUNTIME_PIN_PATH_INVALID')
            seen.add(rel)
            p = _canonical(root / rel)
            stamp = _stamp(p)
            if stamp[2] != item['size']:
                raise ValueError('HQ_RUNTIME_SIZE_MISMATCH:' + rel)
            if deep:
                key = (str(p), item['sha256'])
                if _VERIFIED.get(key) != stamp:
                    if _sha(p) != item['sha256'] or _stamp(p) != stamp:
                        raise ValueError('HQ_RUNTIME_HASH_MISMATCH:' + rel)
                    _VERIFIED[key] = stamp
        return {'runtimeRevision': RUNTIME_REVISION, 'files': len(rows),
                'hashVerified': bool(deep), 'manifestSha256': _sha(pins_path)}

    def _environment(self):
        # Do not pass ambient model/HTTP credentials, proxies or Python hooks.
        env = {k: v for k, v in os.environ.items()
               if k in {'HOME', 'PATH', 'LANG', 'LC_ALL', 'TMPDIR', 'USER', 'LOGNAME'}}
        env.update(HF_HOME=str(self.root / 'cache/huggingface'), HF_HUB_OFFLINE='1',
                   TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
                   HF_HUB_DISABLE_IMPLICIT_TOKEN='1', DO_NOT_TRACK='1',
                   GRADIO_ANALYTICS_ENABLED='False', TOKENIZERS_PARALLELISM='false',
                   ACESTEP_CHECKPOINTS_DIR=str(self.root / 'source/checkpoints'),
                   PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1',
                   PYTORCH_ENABLE_MPS_FALLBACK='1',
                   MPLCONFIGDIR=str(self.root / 'cache/matplotlib'))
        return env

    @staticmethod
    def _memory():
        # macOS pressure: 1=normal, 2=warning, 4=critical. No free-memory cutoff.
        pressure = int(subprocess.check_output(['/usr/sbin/sysctl', '-n',
            'kern.memorystatus_vm_pressure_level'], text=True, timeout=3).strip())
        swap = subprocess.check_output(['/usr/sbin/sysctl', '-n', 'vm.swapusage'], text=True, timeout=3)
        match = re.search(r'used\s*=\s*([0-9.]+)([MG])', swap)
        if pressure not in (1, 2, 4) or not match:
            raise RuntimeError('HQ_MEMORY_MONITOR_UNAVAILABLE')
        gib = float(match[1]) / (1024 if match[2] == 'M' else 1)
        return {'pressure': pressure, 'swapGiB': gib}

    @staticmethod
    def _stop(process):
        if process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=8)

    @contextmanager
    def _locks(self):
        if self.lock_fds:
            self.lock_validator()
            yield self.lock_fds
            return
        p = _canonical(self.root / 'cache/gateway-generation.lock')
        fd = os.open(p, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            s = os.fstat(fd)
            if (not stat.S_ISREG(s.st_mode) or s.st_uid != os.getuid() or
                    s.st_nlink != 1 or s.st_mode & 0o022 or
                    (s.st_dev, s.st_ino) != (p.stat().st_dev, p.stat().st_ino)):
                raise ValueError('HQ_INVALID_SHARED_LOCK')
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError('HQ_SHARED_BUSY_NO_RETRY') from exc
            yield (fd,)
        finally:
            # Child inherits the same open description; never release with LOCK_UN.
            os.close(fd)

    def _stage(self, name, request_path, fds, cancelled, deadline, baseline):
        if self.lock_validator:
            self.lock_validator()
        if cancelled():
            raise Cancelled()
        if time.monotonic() >= deadline:
            raise TimeoutError('HQ_PIPELINE_TIMEOUT')
        peak = baseline['swapGiB']; critical = 0; started = time.monotonic()
        with request_path.with_name(name + '.log').open('xb') as log:
            process = subprocess.Popen([str(self.python), '-B', str(self.runner), name, str(request_path)],
                cwd=self.root / 'source', env=self._environment(), stdin=subprocess.DEVNULL,
                stdout=log, stderr=log, start_new_session=True, pass_fds=fds)
            try:
                while process.poll() is None:
                    if cancelled():
                        raise Cancelled()
                    if time.monotonic() >= deadline:
                        raise TimeoutError('HQ_PIPELINE_TIMEOUT')
                    sample = self._memory(); peak = max(peak, sample['swapGiB'])
                    critical = critical + 1 if sample['pressure'] == 4 else 0
                    if critical >= 3 or sample['swapGiB'] >= 16 or sample['swapGiB'] - baseline['swapGiB'] >= 8:
                        raise RuntimeError('HQ_MEMORY_PRESSURE_STOP')
                    if shutil.disk_usage(self.root).free < 10 * 1024**3:
                        raise RuntimeError('HQ_DISK_LOW_STOP')
                    time.sleep(2)
                if process.returncode:
                    raise RuntimeError('HQ_STAGE_FAILED:' + name + ':' + str(process.returncode))
                if cancelled():
                    raise Cancelled()
            except BaseException:
                self._stop(process)
                raise
        return {'stage': name, 'pid': process.pid, 'exitCode': process.returncode,
                'seconds': time.monotonic() - started, 'peakObservedSwapGiB': peak}

    def generate(self, prompt, duration, seed, model, output, cancelled, settings=None):
        settings = validate_call(prompt, duration, seed, model, settings)
        if not (enabled() if self.activation is None else self.activation is True):
            raise RuntimeError('HQ_PROFILE_NOT_ACTIVATED')
        output = _canonical(output, exists=False)
        if not output.is_relative_to(self.artifacts) or output.suffix != '.wav' or output.exists():
            raise ValueError('HQ_NEW_ARTIFACT_WAV_REQUIRED')
        if cancelled():
            raise Cancelled()
        output.parent.mkdir(parents=True, exist_ok=True)
        work = output.parent / (output.stem + '.hq')
        work.mkdir(mode=0o700, exist_ok=False)  # A previous uncertain attempt cannot replay.
        request = dict(profile_version=PROFILE_VERSION, parent_pid=os.getpid(),
                       runtime_root=str(self.root), output=str(work / 'audio.wav'),
                       prompt=prompt, duration=duration, seed=seed, settings=settings)
        request['request_sha256'] = hashlib.sha256(canonical(request).encode()).hexdigest()
        request_path = work / 'request.json'; _write_new(request_path, request)
        started = time.monotonic(); stages = []
        try:
            with self._locks() as fds:
                pins = self.verify_runtime(self.root, deep=True)
                baseline = self._memory()
                if baseline['pressure'] == 4 or baseline['swapGiB'] >= 16:
                    raise RuntimeError('HQ_MEMORY_UNSAFE_BEFORE_START')
                if shutil.disk_usage(self.root).free < 10 * 1024**3:
                    raise RuntimeError('HQ_DISK_LOW_STOP')
                deadline = time.monotonic() + self.timeout
                stages.append(self._stage('plan', request_path, fds, cancelled,
                                          min(deadline, time.monotonic() + 240), baseline))
                plan = _read_json(work / 'lm-result.json')
                expected = dict(schemaVersion='ace-hq-plan.v1', success=True,
                    profileVersion=PROFILE_VERSION, requestSha256=request['request_sha256'],
                    model='acestep-5Hz-lm-4B', modelRevision=LM_REVISION,
                    runtimeRevision=RUNTIME_REVISION, backend='mlx', dtype='bfloat16',
                    seed=seed, duration=duration, plannerPid=stages[0]['pid'])
                for key, value in expected.items():
                    if type(plan.get(key)) is not type(value) or plan[key] != value:
                        raise ValueError('HQ_PLAN_BINDING_MISMATCH:' + key)
                # Process has exited, not merely returned a response or unloaded a slot.
                self.verify_runtime(self.root, deep=True)
                stages.append(self._stage('render', request_path, fds, cancelled, deadline, baseline))
                receipt = _read_json(work / 'render-result.json')
                expected = dict(schemaVersion='ace-hq-render.v1', success=True,
                    profileVersion=PROFILE_VERSION, requestSha256=request['request_sha256'],
                    model='acestep-v15-xl-sft', modelRevision=XL_REVISION,
                    runtimeRevision=RUNTIME_REVISION, backend='mps', dtype='float32',
                    lmResultSha256=_sha(work / 'lm-result.json'), seed=seed,
                    sampleRate=48000, channels=2)
                for key, value in expected.items():
                    if type(receipt.get(key)) is not type(value) or receipt[key] != value:
                        raise ValueError('HQ_RENDER_BINDING_MISMATCH:' + key)
                audio = _canonical(work / 'audio.wav')
                with wave.open(str(audio), 'rb') as wav:
                    actual = wav.getnframes() / wav.getframerate()
                    if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth(), wav.getcomptype()) != (48000, 2, 2, 'NONE') or abs(actual - duration) > .1:
                        raise ValueError('HQ_WAV_INVALID')
                if (receipt.get('audioSha256') != _sha(audio) or receipt.get('audioBytes') != audio.stat().st_size or
                        type(receipt.get('durationSeconds')) not in (int, float) or abs(receipt['durationSeconds'] - actual) > 1e-6):
                    raise ValueError('HQ_AUDIO_RECEIPT_MISMATCH')
                if cancelled():
                    raise Cancelled()
                with audio.open('rb') as source, output.open('xb') as dest:
                    shutil.copyfileobj(source, dest); dest.flush(); os.fsync(dest.fileno())
                result = dict(synthetic_test_artifact=False, provider='ace-step', model=MODEL,
                    model_version=PROFILE_VERSION, license='MIT (ACE); Apache-2.0 upstream Qwen notices',
                    runtime='sequential MLX BF16 LM4B -> process exit -> MPS FP32 XL-SFT',
                    runtime_commit=RUNTIME_REVISION, planner_snapshot=LM_REVISION,
                    weights_snapshot=XL_REVISION, settings=settings,
                    fixed_settings={'inference_steps': 50, 'guidance_scale': 7.0, 'shift': 3.0,
                                    'normalization_db': -1.0, 'quantization': None},
                    sample_rate=48000, channels=2, duration_seconds=actual,
                    generation_seconds=time.monotonic() - started, stages=stages,
                    request_sha256=request['request_sha256'], audio_sha256=receipt['audioSha256'],
                    runtime_validation=pins, allocator_limits='OS defaults')
                _write_new(work / 'completion.json', result)
                return result
        except BaseException as exc:
            _write_new(work / 'failed.json', {'status': 'failed-no-auto-retry',
                'errorType': type(exc).__name__, 'error': str(exc), 'stages': stages})
            raise
