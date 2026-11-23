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
import select
import shutil
import signal
import stat
import subprocess
import time
import wave

from .backends import Cancelled
from .hq_contract import (MODEL, PROFILE_VERSION, RUNTIME_REVISION, LM_REVISION,
                          XL_REVISION, TARGET_DURATION_KEY, TARGET_PLAN_ATTEMPTS,
                          canonical, enabled, resolved_auto_duration, target_plan_seed, validate_call)

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


# Only classification survives scratch cleanup; never include child log lines,
# prompts, model output, runtime paths or timings in the outward diagnostic.
_CHILD_CODES = frozenset((
    'HQ_LM_AUTO_DURATION_INVALID', 'HQ_LM_AUTO_DURATION_MISSING', 'HQ_LM_AUTO_DURATION_MALFORMED',
    'HQ_LM_AUTO_DURATION_OUT_OF_RANGE', 'HQ_LM_AUTO_DURATION_CAP_UNAVAILABLE', 'HQ_LM_METADATA_INVALID', 'HQ_LM_GENERATION_FAILED',
    'HQ_LM_AUDIO_CODES_INVALID', 'HQ_LM_AUDIO_CODE_RANGE_INVALID',
    'HQ_BPM_INVALID', 'HQ_TIME_SIGNATURE_INVALID', 'HQ_SETTING_TEXT_INVALID',
    'HQ_LM_MLX_INITIALIZATION_REQUIRED', 'HQ_MPS_REQUIRED', 'HQ_MPS_FP32_RENDERER_REQUIRED',
    'HQ_REQUIRED_COMPONENT_MISSING', 'HQ_RENDER_GENERATION_FAILED',
    'HQ_GENERATED_OUTPUT_PATH_INVALID', 'HQ_WAV_FORMAT_INVALID', 'HQ_WAV_DURATION_INVALID',
    'HQ_PLANNER_BINDING_MISMATCH', 'HQ_REQUEST_HASH_MISMATCH',
))
_LOCAL_STAGE_CODES = frozenset((
    'HQ_PIPELINE_TIMEOUT', 'HQ_MEMORY_PRESSURE_STOP', 'HQ_MEMORY_MONITOR_UNAVAILABLE', 'HQ_DISK_LOW_STOP',
))


def _child_code(path, exit_status):
    if exit_status < 0:
        return 'HQ_CHILD_SIGNAL'
    with path.open('rb') as f:
        f.seek(max(0, os.fstat(f.fileno()).st_size - 8192))
        tail = f.read(8192).decode('utf-8', 'replace')
    # The terminal exception (not arbitrary log lines) can be mapped to known errors.
    terminal = tail.rstrip().split('\n')[-1].strip()
    found = re.fullmatch(r'(?:ValueError|RuntimeError|TypeError|AssertionError): (HQ_[A-Z0-9_]+)(?::[^\r\n]*)?', terminal)
    if found and found[1] in _CHILD_CODES:
        return found[1]
    if (terminal.startswith(('RuntimeError: ', 'torch.OutOfMemoryError: '))
            and ('MPS backend out of memory' in terminal or "DefaultCPUAllocator: can't allocate memory" in terminal)):
        return 'HQ_CHILD_OUT_OF_MEMORY'
    return 'HQ_CHILD_NONZERO_EXIT'


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
        self.resident = None
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

    def enable_resident(self):
        if len(self.lock_fds)!=2 or self.resident is not None:
            raise ValueError('HQ_RESIDENT_LOCK_REQUIRED')
        self.resident = {}

    def close_resident(self):
        if self.resident is None:
            return
        children=list(self.resident.values())
        self.resident.clear()
        for process, _ in children:
            try:
                if process.stdin:
                    process.stdin.close()
            except OSError:
                pass
        # Both groups may be alive. Use shared shutdown budgets, not one timer
        # per process, so the parent can await both before releasing its locks.
        for process, _ in children:
            if process.poll() is None:
                try:
                    os.killpg(process.pid,signal.SIGTERM)
                except ProcessLookupError:
                    pass
        deadline=time.monotonic()+8
        for process, _ in children:
            try:
                process.wait(timeout=max(0,deadline-time.monotonic()))
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError:
                    pass
        deadline=time.monotonic()+8
        fault=None
        for process, fd in children:
            try:
                process.wait(timeout=max(0,deadline-time.monotonic()))
            except subprocess.TimeoutExpired as exc:
                fault=exc
            finally:
                os.close(fd)
        if fault:
            raise RuntimeError('HQ_RESIDENT_CHILD_EXITED') from fault

    def _resident_process(self, name, fds):
        process_info = self.resident.get(name)
        if process_info is not None:
            if process_info[0].poll() is not None:
                raise RuntimeError('HQ_RESIDENT_CHILD_EXITED')
            return process_info
        fd_read, fd_write = os.pipe()
        try:
            argv=[str(self.python),'-B',str(self.runner),'--resident',name,str(self.root),str(os.getpid()),str(fd_write)]
            process=subprocess.Popen(argv,cwd=self.root/'source',env=self._environment(),
                stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                start_new_session=True,pass_fds=(*fds,fd_write))
        except BaseException:
            os.close(fd_read)
            raise
        finally:
            os.close(fd_write)
        process_info=(process,fd_read)
        self.resident[name]=process_info
        return process_info

    def _resident_stage(self, name, request_path, fds, cancelled, deadline, baseline):
        if self.lock_validator:
            self.lock_validator()
        if cancelled():
            raise Cancelled()
        if time.monotonic() >= deadline:
            raise TimeoutError('HQ_PIPELINE_TIMEOUT')
        process, fd = self._resident_process(name,fds)
        request_sha = _read_json(request_path)['request_sha256']
        frame=(canonical({'path':str(request_path),'requestSha256':request_sha})+'\n').encode()
        if len(frame)>4096:
            raise ValueError('HQ_RESIDENT_REQUEST_FRAME')
        started=time.monotonic()
        critical=0
        peak=baseline['swapGiB']
        buf=bytearray()
        try:
            process.stdin.write(frame)
            process.stdin.flush()
            while True:
                if cancelled():
                    raise Cancelled()
                remaining=deadline-time.monotonic()
                if remaining<=0:
                    raise TimeoutError('HQ_PIPELINE_TIMEOUT')
                readable,_,_=select.select([fd],[],[],min(2,remaining))
                if readable:
                    chunk=os.read(fd,4097-len(buf))
                    if not chunk:
                        raise RuntimeError('HQ_RESIDENT_CHILD_EXITED')
                    buf.extend(chunk)
                    if len(buf)>4096:
                        raise RuntimeError('HQ_RESIDENT_REPLY_INVALID')
                    if b'\n' in buf:
                        if buf.count(b'\n')!=1 or not buf.endswith(b'\n'):
                            raise RuntimeError('HQ_RESIDENT_REPLY_INVALID')
                        try:
                            reply=json.loads(buf)
                        except (ValueError, UnicodeError) as exc:
                            raise RuntimeError('HQ_RESIDENT_REPLY_INVALID') from exc
                        if type(reply) is not dict or reply.get('requestSha256')!=request_sha:
                            raise RuntimeError('HQ_RESIDENT_REPLY_INVALID')
                        if reply.get('ok') is False and set(reply)=={'requestSha256','ok','code'}:
                            code=reply['code']
                            allow=_CHILD_CODES|{'HQ_CHILD_OUT_OF_MEMORY','HQ_CHILD_NONZERO_EXIT','HQ_RESIDENT_JOB_BINDING','HQ_RESIDENT_REQUEST_FRAME'}
                            raise RuntimeError('HQ_STAGE_DIAGNOSTIC:'+name+':'+(code if isinstance(code,str) and code in allow else 'HQ_CHILD_NONZERO_EXIT'))
                        if reply.get('ok') is not True or set(reply)!={'requestSha256','ok'}:
                            raise RuntimeError('HQ_RESIDENT_REPLY_INVALID')
                        if cancelled():
                            raise Cancelled()
                        return {'stage':name,'pid':process.pid,'exitCode':None,'resident':True,
                            'seconds':time.monotonic()-started,'peakObservedSwapGiB':peak}
                else:
                    # Preserve the existing active-generation safety checks only.
                    sample=self._memory();peak=max(peak,sample['swapGiB'])
                    critical=critical+1 if sample['pressure']==4 else 0
                    if critical>=3 or sample['swapGiB']>=16 or sample['swapGiB']-baseline['swapGiB']>=8:
                        raise RuntimeError('HQ_MEMORY_PRESSURE_STOP')
                    if shutil.disk_usage(self.root).free < 10 * 1024**3:
                        raise RuntimeError('HQ_DISK_LOW_STOP')
        except BaseException as exc:
            code='HQ_RESIDENT_CHILD_EXITED' if isinstance(exc,BrokenPipeError) else str(exc) if isinstance(exc,(RuntimeError,TimeoutError)) else ''
            if code in _LOCAL_STAGE_CODES or code in ('HQ_RESIDENT_CHILD_EXITED','HQ_RESIDENT_REPLY_INVALID'):
                raise RuntimeError('HQ_STAGE_DIAGNOSTIC:'+name+':'+code) from exc
            raise

    def _stage(self, name, request_path, fds, cancelled, deadline, baseline):
        if self.resident is not None:
            return self._resident_stage(name, request_path, fds, cancelled, deadline, baseline)
        if self.lock_validator:
            self.lock_validator()
        if cancelled():
            raise Cancelled()
        if time.monotonic() >= deadline:
            raise TimeoutError('HQ_PIPELINE_TIMEOUT')
        peak = baseline['swapGiB']; critical = 0; started = time.monotonic()
        log_path = request_path.with_name(name + '.log')
        with log_path.open('xb') as log:
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
                    raise RuntimeError('HQ_STAGE_DIAGNOSTIC:' + name + ':' + _child_code(log_path, process.returncode))
                if cancelled():
                    raise Cancelled()
            except BaseException as exc:
                self._stop(process)
                stage_code = str(exc) if isinstance(exc, (RuntimeError, TimeoutError)) else ''
                if stage_code in _LOCAL_STAGE_CODES:
                    raise RuntimeError('HQ_STAGE_DIAGNOSTIC:' + name + ':' + stage_code) from exc
                raise
        return {'stage': name, 'pid': process.pid, 'exitCode': process.returncode,
                'seconds': time.monotonic() - started, 'peakObservedSwapGiB': peak}

    def generate(self, prompt, duration, seed, model, output, cancelled, settings=None):
        settings = validate_call(prompt, duration, seed, model, settings)
        if not (enabled() if self.activation is None else self.activation is True):
            raise RuntimeError('HQ_PROFILE_NOT_ACTIVATED')
        output = _canonical(output, exists=False)
        if (not output.is_relative_to(self.artifacts) or output.suffix != '.wav' or output.exists()
                or self.resident is not None and (output.parent.parent!=self.artifacts or not output.parent.name.startswith('content.workflow-bgm-'))):
            raise ValueError('HQ_NEW_ARTIFACT_WAV_REQUIRED')
        if cancelled():
            raise Cancelled()
        output.parent.mkdir(parents=True, exist_ok=True)
        work = output.parent / (output.stem + '.hq')
        work.mkdir(mode=0o700, exist_ok=False)  # A previous uncertain attempt cannot replay.
        target = settings.get(TARGET_DURATION_KEY)
        def prepare(attempt):
            stage_dir = work if target is None else work / f'attempt-{attempt}'
            if target is not None:
                stage_dir.mkdir(mode=0o700, exist_ok=False)
            attempt_seed = target_plan_seed(seed, attempt) if target is not None else seed
            request = dict(profile_version=PROFILE_VERSION, parent_pid=os.getpid(),
                           runtime_root=str(self.root), output=str(stage_dir / 'audio.wav'),
                           prompt=prompt, duration=duration, seed=attempt_seed, settings=settings)
            request['request_sha256'] = hashlib.sha256(canonical(request).encode()).hexdigest()
            request_path = stage_dir / 'request.json'; _write_new(request_path, request)
            return stage_dir, request_path, request

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
                selected = None
                for attempt in range(1, 2 if target is None else TARGET_PLAN_ATTEMPTS + 1):
                    stage_dir, request_path, request = prepare(attempt)
                    stage = self._stage('plan', request_path, fds, cancelled,
                                        min(deadline, time.monotonic() + 240), baseline)
                    stages.append(stage)
                    plan = _read_json(stage_dir / 'lm-result.json')
                    expected = dict(schemaVersion='ace-hq-plan.v1', success=True,
                        profileVersion=PROFILE_VERSION, requestSha256=request['request_sha256'],
                        model='acestep-5Hz-lm-4B', modelRevision=LM_REVISION,
                        runtimeRevision=RUNTIME_REVISION, backend='mlx', dtype='bfloat16',
                        seed=request['seed'], plannerPid=stage['pid'])
                    for key, value in expected.items():
                        if type(plan.get(key)) is not type(value) or plan[key] != value:
                            raise ValueError('HQ_PLAN_BINDING_MISMATCH:' + key)
                    planned = duration
                    if planned is None:
                        metadata = plan.get('metadata')
                        if not isinstance(metadata, dict):
                            raise ValueError('HQ_LM_METADATA_INVALID')
                        planned = resolved_auto_duration(metadata.get('duration'))
                    if type(plan.get('duration')) is not type(planned) or plan['duration'] != planned:
                        raise ValueError('HQ_PLAN_BINDING_MISMATCH:duration')
                    if target is None or planned >= target:
                        selected = planned, attempt, stage_dir, request_path, request
                        break
                if selected is None:
                    raise ValueError('HQ_LM_FULL_DURATION_NOT_MET')
                planned, selected_attempt, stage_dir, request_path, request = selected
                # One-shot plan exits. Resident planner replies only after the
                # bound plan is fully written and its model invocation returned.
                self.verify_runtime(self.root, deep=True)
                stages.append(self._stage('render', request_path, fds, cancelled, deadline, baseline))
                receipt = _read_json(stage_dir / 'render-result.json')
                expected = dict(schemaVersion='ace-hq-render.v1', success=True,
                    profileVersion=PROFILE_VERSION, requestSha256=request['request_sha256'],
                    model='acestep-v15-xl-sft', modelRevision=XL_REVISION,
                    runtimeRevision=RUNTIME_REVISION, backend='mps', dtype='float32',
                    lmResultSha256=_sha(stage_dir / 'lm-result.json'), seed=request['seed'],
                    sampleRate=48000, channels=2)
                for key, value in expected.items():
                    if type(receipt.get(key)) is not type(value) or receipt[key] != value:
                        raise ValueError('HQ_RENDER_BINDING_MISMATCH:' + key)
                audio = _canonical(stage_dir / 'audio.wav')
                with wave.open(str(audio), 'rb') as wav:
                    actual = wav.getnframes() / wav.getframerate()
                    if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth(), wav.getcomptype()) != (48000, 2, 2, 'NONE') or abs(actual - planned) > .1:
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
                    runtime=('resident sequential MLX BF16 LM4B -> MPS FP32 XL-SFT' if self.resident is not None
                             else 'sequential MLX BF16 LM4B -> process exit -> MPS FP32 XL-SFT'),
                    runtime_commit=RUNTIME_REVISION, planner_snapshot=LM_REVISION,
                    weights_snapshot=XL_REVISION, settings=settings,
                    fixed_settings={'inference_steps': 50, 'guidance_scale': 7.0, 'shift': 3.0,
                                    'normalization_db': -1.0, 'quantization': None},
                    sample_rate=48000, channels=2, duration_seconds=actual,
                    generation_seconds=time.monotonic() - started, stages=stages,
                    request_sha256=request['request_sha256'], audio_sha256=receipt['audioSha256'],
                    runtime_validation=pins, allocator_limits='OS defaults')
                if target is not None:
                    result.update(plan_attempt=selected_attempt, seed=request['seed'])
                _write_new(work / 'completion.json', result)
                return result
        except BaseException as exc:
            _write_new(work / 'failed.json', {'status': 'failed-no-auto-retry',
                'errorType': type(exc).__name__, 'error': str(exc), 'stages': stages})
            raise
