"""Two isolated HQ child stages; importing this module never loads a model.

CLI: python ace_hq_runner.py plan|render REQUEST_JSON
The parent owns admission, runtime/hash validation, locks, resource guards and
waiting for the planning process to exit before starting the renderer.
"""
from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import shutil
import signal
import stat
import sys
import threading
import time
import wave

PROFILE_VERSION = 'ace-xl-sft-lm4b.v1'
RUNTIME_REVISION = 'ca1e85fe9430179831e6bc6be790c332190a3866'
COVER_CAPTION = ('Instrumental arrangement preserving the reference melody, harmony, rhythm, '
                 'instrumentation, dynamics, and section transitions.')
COVER_STRENGTH = .8
COVER_SOURCE_KEY = 'cover_source_sha256'
SEPARATOR_WEIGHTS = 'hdemucs_high_trained.pt'
LM_MODEL = 'acestep-5Hz-lm-4B'
LM_REVISION = '0a3ec94b557aea7d508da38b31cfe7341f6ff737'
DIT_MODEL = 'acestep-v15-xl-sft'
DIT_REVISION = 'd06de46b4622f781cf07f4a013a67d591ca52819'
AUTO_MAX_SECONDS = 120
CODE_PATTERN = re.compile(r'(?:<\|audio_code_[0-9]{1,6}\|>)+')


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def sha256(value):
    return hashlib.sha256(value).hexdigest()


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('HQ_DUPLICATE_JSON_KEY')
        result[key] = value
    return result


def load_json(path, limit=1024 * 1024):
    path = Path(path)
    if path.resolve() != path or not path.is_file() or path.stat().st_nlink != 1:
        raise ValueError('HQ_JSON_PATH_INVALID')
    if path.stat().st_size > limit:
        raise ValueError('HQ_JSON_TOO_LARGE')
    raw = path.read_bytes()
    value = json.loads(raw, object_pairs_hook=_object,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError('HQ_NONFINITE_JSON')))
    if not isinstance(value, dict):
        raise ValueError('HQ_JSON_OBJECT_REQUIRED')
    return value, raw


def write_json_new(path, value):
    data = canonical(value) + b'\n'
    with Path(path).open('xb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def validate_codes(value):
    if not isinstance(value, str) or not 0 < len(value) <= 100000 or not CODE_PATTERN.fullmatch(value):
        raise ValueError('HQ_LM_AUDIO_CODES_INVALID')
    if any(int(token) > 65535 for token in re.findall(r'audio_code_([0-9]+)', value)):
        raise ValueError('HQ_LM_AUDIO_CODE_RANGE_INVALID')
    return value


def _text(value, maximum, empty=True):
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()):
        raise ValueError('HQ_SETTING_TEXT_INVALID')
    if '\x00' in value:
        raise ValueError('HQ_SETTING_TEXT_INVALID')
    return value


def musical_fields(settings, metadata=None):
    """Consume only known scalar fields, never arbitrary LM kwargs or paths."""
    metadata = metadata or {}
    fields = {}
    for key, maximum in [('keyscale', 32), ('timesignature', 1), ('vocal_language', 16)]:
        value = settings.get(key, metadata.get(key))
        if value is not None:
            fields[key] = _text(value, maximum)
            if key == 'timesignature' and value not in ('2', '3', '4', '6', '7'):
                raise ValueError('HQ_TIME_SIGNATURE_INVALID')
    bpm = settings.get('bpm', metadata.get('bpm'))
    if bpm is not None:
        if type(bpm) not in (int, float) or not math.isfinite(bpm) or int(bpm) != bpm or not 30 <= bpm <= 300:
            raise ValueError('HQ_BPM_INVALID')
        fields['bpm'] = int(bpm)
    return fields


def resolved_duration(request, metadata):
    value = request['duration']
    if value is not None:
        return value
    value = metadata.get('duration')
    if value is None:
        raise ValueError('HQ_LM_AUTO_DURATION_MISSING')
    # ACE's parser returns int for integer strings and otherwise preserves text;
    # Phase 2 accepts float(), so permit simple decimal seconds without interpreting units.
    if isinstance(value, str) and re.fullmatch(r'[0-9]{1,3}(?:\.[0-9]{1,6})?', value.strip()):
        value = float(value.strip())
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError('HQ_LM_AUTO_DURATION_MALFORMED')
    if not 10 <= value <= AUTO_MAX_SECONDS:
        raise ValueError('HQ_LM_AUTO_DURATION_OUT_OF_RANGE')
    return value


@contextmanager
def scoped_lm_duration_vocabulary(lm, request):
    """Bind the pinned ACE numeric FSM to this request before CoT sampling."""
    if request['duration'] is not None:
        yield
        return
    processor = getattr(lm, 'constrained_processor', None)
    existing_max = getattr(processor, 'max_duration', None)
    specs = getattr(processor, 'field_specs', None)
    spec = specs.get('duration') if isinstance(specs, dict) else None
    if (type(existing_max) is not int or not isinstance(spec, dict)
            or type(spec.get('min')) is not int or spec.get('max') != existing_max
            or not hasattr(processor, 'valid_duration_values')
            or not hasattr(processor, 'duration_prefix_tree')
            or not callable(getattr(processor, '_build_numeric_prefix_tree', None))):
        raise RuntimeError('HQ_LM_AUTO_DURATION_CAP_UNAVAILABLE')
    lower = max(spec['min'], request['settings'].get('target_duration_seconds', 10))
    upper = min(existing_max, AUTO_MAX_SECONDS)
    if lower > upper:
        raise RuntimeError('HQ_LM_AUTO_DURATION_CAP_UNAVAILABLE')
    prior_spec = dict(spec)
    prior_values = processor.valid_duration_values
    prior_tree = processor.duration_prefix_tree
    try:
        values = [str(value) for value in range(lower, upper + 1)]
        tree = processor._build_numeric_prefix_tree(
            values, context_prefix_for_matching='duration:',
            context_prefix_for_tokenization='duration: ')
        if not tree:
            raise RuntimeError('HQ_LM_AUTO_DURATION_CAP_UNAVAILABLE')
        spec.update(min=lower, max=upper)
        processor.max_duration = upper
        processor.valid_duration_values = values
        processor.duration_prefix_tree = tree
        yield
    finally:
        spec.clear()
        spec.update(prior_spec)
        processor.max_duration = existing_max
        processor.valid_duration_values = prior_values
        processor.duration_prefix_tree = prior_tree


def lm_options(request):
    duration = request['duration']
    user_metadata = musical_fields(request['settings'])
    if duration is not None:
        user_metadata = {'duration': duration, **user_metadata}
    return dict(caption=request['prompt'], lyrics=_lyrics(request['settings']),
                infer_type='llm_dit', temperature=.85, cfg_scale=2.0,
                negative_prompt='NO USER INPUT', top_k=None, top_p=.9,
                target_duration=duration, user_metadata=user_metadata,
                use_cot_metas=duration is None, use_cot_caption=False, use_cot_language=False,
                use_constrained_decoding=True, batch_size=1, seeds=[request['seed']])


def validate_request(value, request_path):
    if value.get('profile_version') != PROFILE_VERSION:
        raise ValueError('HQ_PROFILE_VERSION_REQUIRED')
    claimed = value.get('request_sha256')
    if not isinstance(claimed, str) or not re.fullmatch('[a-f0-9]{64}', claimed):
        raise ValueError('HQ_REQUEST_HASH_REQUIRED')
    unsigned = {k: v for k, v in value.items() if k != 'request_sha256'}
    if sha256(canonical(unsigned)) != claimed:
        raise ValueError('HQ_REQUEST_HASH_MISMATCH')
    if type(value.get('parent_pid')) is not int or value['parent_pid'] <= 1:
        raise ValueError('HQ_PARENT_PID_INVALID')
    if type(value.get('seed')) is not int or not 0 <= value['seed'] <= 2**31 - 1:
        raise ValueError('HQ_SEED_INVALID')
    duration = value.get('duration')
    cover = COVER_SOURCE_KEY in value
    if cover:
        if (type(value[COVER_SOURCE_KEY]) is not str or not re.fullmatch(r'[a-f0-9]{64}',value[COVER_SOURCE_KEY])
                or value.get('settings')!={'instrumental':True} or value.get('prompt') is not None
                or type(duration) not in (int,float) or not math.isfinite(duration) or not 10<=duration<=AUTO_MAX_SECONDS):
            raise ValueError('HQ_COVER_SOURCE_INVALID')
    else:
        if duration is not None and (type(duration) not in (int,float) or not math.isfinite(duration) or not 10<=duration<=30):
            raise ValueError('HQ_DURATION_INVALID')
        _text(value.get('prompt'),2000,empty=False)
    settings = value.get('settings')
    if not isinstance(settings, dict) or set(settings) - {'instrumental', 'lyrics', 'bpm', 'keyscale', 'timesignature', 'vocal_language', 'target_duration_seconds'}:
        raise ValueError('HQ_SETTINGS_INVALID')
    if 'target_duration_seconds' in settings and (duration is not None
            or type(settings['target_duration_seconds']) is not int or settings['target_duration_seconds'] != 60):
        raise ValueError('HQ_TARGET_DURATION_INVALID')
    if type(settings.get('instrumental', True)) is not bool:
        raise ValueError('HQ_INSTRUMENTAL_INVALID')
    lyrics = _text(settings.get('lyrics', ''), 4000)
    if settings.get('instrumental', True) and lyrics:
        raise ValueError('HQ_INSTRUMENTAL_LYRICS_CONFLICT')
    if not settings.get('instrumental', True) and not lyrics.strip():
        raise ValueError('HQ_VOCAL_LYRICS_REQUIRED')
    musical_fields(settings)
    root, output = Path(value['runtime_root']), Path(value['output'])
    if not root.is_absolute() or root.resolve() != root or not (root / 'source').is_dir():
        raise ValueError('HQ_RUNTIME_ROOT_INVALID')
    if not output.is_absolute() or output.resolve() != output or output.suffix != '.wav':
        raise ValueError('HQ_OUTPUT_PATH_INVALID')
    if output.parent != Path(request_path).parent or not output.parent.is_dir() or output.exists():
        raise ValueError('HQ_NEW_JOB_OUTPUT_REQUIRED')
    return root, output


def require_components(checkpoints):
    required = [
        f'{DIT_MODEL}/model.safetensors.index.json',
        *[f'{DIT_MODEL}/model-{i:05d}-of-00004.safetensors' for i in range(1, 5)],
        f'{DIT_MODEL}/config.json', f'{DIT_MODEL}/silence_latent.pt',
        f'{DIT_MODEL}/configuration_acestep_v15.py',
        f'{DIT_MODEL}/modeling_acestep_v15_xl_base.py', f'{DIT_MODEL}/apg_guidance.py',
        f'{LM_MODEL}/model.safetensors.index.json',
        *[f'{LM_MODEL}/model-{i:05d}-of-00002.safetensors' for i in range(1, 3)],
        f'{LM_MODEL}/config.json', f'{LM_MODEL}/tokenizer.json',
        'Qwen3-Embedding-0.6B/model.safetensors', 'Qwen3-Embedding-0.6B/tokenizer.json',
        'vae/diffusion_pytorch_model.safetensors', 'vae/config.json',
    ]
    for name in required:
        path = checkpoints / name
        if path.resolve() != path or not path.is_file() or not path.stat().st_size:
            raise ValueError('HQ_REQUIRED_COMPONENT_MISSING:' + name)


def offline_environment(root):
    for key in ('PYTORCH_MPS_HIGH_WATERMARK_RATIO', 'PYTORCH_MPS_LOW_WATERMARK_RATIO'):
        os.environ.pop(key, None)
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                      HF_HUB_DISABLE_TELEMETRY='1', HF_HUB_DISABLE_IMPLICIT_TOKEN='1',
                      DO_NOT_TRACK='1', GRADIO_ANALYTICS_ENABLED='False',
                      TOKENIZERS_PARALLELISM='false',
                      HF_HOME=str(root / 'cache/huggingface'),
                      ACESTEP_CHECKPOINTS_DIR=str(root / 'source/checkpoints'))


def watch_parent(parent_pid):
    # Refuse group signalling unless this child owns the group created by its
    # parent; never terminate an inherited interactive shell/process group.
    if os.getpgrp() != os.getpid():
        raise ValueError('HQ_OWN_PROCESS_GROUP_REQUIRED')
    def watch():
        while True:
            if os.getppid() != parent_pid:
                os.killpg(os.getpid(), signal.SIGTERM)
                return
            time.sleep(1)
    threading.Thread(target=watch, daemon=True).start()


def _lyrics(settings):
    return '[Instrumental]' if settings.get('instrumental', True) else settings['lyrics']


def run_plan(request, root, output, resident=None):
    if COVER_SOURCE_KEY in request:raise ValueError('HQ_COVER_SOURCE_INVALID')
    import torch
    import numpy as np
    import mlx.core as mx
    from acestep.llm_inference import LLMHandler
    if not torch.backends.mps.is_available():
        raise RuntimeError('HQ_MPS_REQUIRED')
    lm = resident.get('lm') if resident is not None else None
    started = time.monotonic()
    native_max = None
    try:
        if lm is None:
            lm = LLMHandler()
            status, ok = lm.initialize(checkpoint_dir=str(root / 'source/checkpoints'),
                                      lm_model_path=LM_MODEL, backend='mlx', device='mps',
                                      offload_to_cpu=False, dtype=torch.bfloat16)
            if not ok or lm.llm_backend != 'mlx':
                raise RuntimeError('HQ_LM_MLX_INITIALIZATION_REQUIRED')
            if resident is not None:
                resident['lm'] = lm
                resident['native_max'] = lm.constrained_processor.max_duration
        if resident is not None:
            native_max = resident['native_max']
            # Each command starts with the hardware-approved vocabulary; auto
            # requests may then lower it, never leaking that state into a fixed job.
            lm.constrained_processor.set_max_duration(native_max)
        random.seed(request['seed']); np.random.seed(request['seed'])
        torch.manual_seed(request['seed']); mx.random.seed(request['seed'])
        options = lm_options(request)
        write_json_new(output.parent / 'lm-options.json', options)
        with scoped_lm_duration_vocabulary(lm, request):
            raw = lm.generate_with_stop_condition(**options)
        if not isinstance(raw, dict) or raw.get('success') is not True:
            raise RuntimeError('HQ_LM_GENERATION_FAILED')
        codes = validate_codes(raw.get('audio_codes'))
        metadata = raw.get('metadata')
        if not isinstance(metadata, dict) or len(canonical(metadata)) > 16384:
            raise ValueError('HQ_LM_METADATA_INVALID')
        musical_fields(request['settings'], metadata)
        planned_duration = resolved_duration(request, metadata)
        # Raw output below is unmodified. In the bound plan, normalize only
        # model-selected duration if returned as a simple decimal string.
        if request['duration'] is None:
            metadata = {**metadata, 'duration': planned_duration}
        envelope = dict(schemaVersion='ace-hq-plan.v1', success=True,
                        profileVersion=PROFILE_VERSION, requestSha256=request['request_sha256'],
                        model=LM_MODEL, modelRevision=LM_REVISION, runtimeRevision=RUNTIME_REVISION,
                        backend='mlx', dtype='bfloat16', plannerPid=os.getpid(),
                        seed=request['seed'], duration=planned_duration,
                        audio_codes=codes, metadata=metadata,
                        elapsedSeconds=time.monotonic() - started)
        # No default=str coercion: unexpected runtime objects must not masquerade
        # as valid replayable model output.
        write_json_new(output.parent / 'raw-model-output.json', raw)
    finally:
        if resident is None and lm is not None:
            lm.unload()
    write_json_new(output.parent / 'lm-result.json', envelope)


def read_plan(request, output):
    plan, raw = load_json(output.parent / 'lm-result.json')
    expected = {'schemaVersion': 'ace-hq-plan.v1', 'success': True,
                'profileVersion': PROFILE_VERSION, 'requestSha256': request['request_sha256'],
                'model': LM_MODEL, 'modelRevision': LM_REVISION, 'runtimeRevision': RUNTIME_REVISION,
                'backend': 'mlx', 'dtype': 'bfloat16', 'seed': request['seed']}
    for key, value in expected.items():
        if type(plan.get(key)) is not type(value) or plan[key] != value:
            raise ValueError('HQ_PLANNER_BINDING_MISMATCH:' + key)
    validate_codes(plan.get('audio_codes'))
    if not isinstance(plan.get('metadata'), dict) or len(canonical(plan['metadata'])) > 16384:
        raise ValueError('HQ_LM_METADATA_INVALID')
    musical_fields(request['settings'], plan['metadata'])
    planned = resolved_duration(request, plan['metadata'])
    if planned < request['settings'].get('target_duration_seconds', 0):
        raise ValueError('HQ_LM_FULL_DURATION_NOT_MET')
    if type(plan.get('duration')) is not type(planned) or plan['duration'] != planned:
        raise ValueError('HQ_PLANNER_BINDING_MISMATCH:duration')
    return plan, sha256(raw)


def wav_details(path, expected_duration):
    with wave.open(str(path), 'rb') as wav:
        duration = wav.getnframes() / wav.getframerate()
        if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth(), wav.getcomptype()) != (48000, 2, 2, 'NONE'):
            raise ValueError('HQ_WAV_FORMAT_INVALID')
        if abs(duration - expected_duration) > .1:
            raise ValueError('HQ_WAV_DURATION_INVALID')
    return duration


def _separator_checkpoint(root):
    directory=root/'cache/separation'
    pin=directory/'pin.json'
    weights=directory/SEPARATOR_WEIGHTS
    try:
        for path in (directory,pin,weights):
            st=path.lstat()
            if (path.resolve(strict=True)!=path or st.st_uid!=os.getuid() or st.st_mode&0o077
                    or (path==directory and not stat.S_ISDIR(st.st_mode))
                    or (path!=directory and (not stat.S_ISREG(st.st_mode) or st.st_nlink!=1))):
                raise ValueError
        if pin.stat().st_size>4096 or not 0<weights.stat().st_size<=1024**3:
            raise ValueError
        metadata,_=load_json(pin,4096)
        if (set(metadata)!={'schemaVersion','checkpointSha256'} or metadata['schemaVersion']!='ace-hq-hdemucs-pin.v1'
                or type(metadata['checkpointSha256']) is not str or not re.fullmatch(r'[a-f0-9]{64}',metadata['checkpointSha256'])):
            raise ValueError
        return weights,metadata['checkpointSha256']
    except (OSError,ValueError,TypeError):
        raise RuntimeError('HQ_SEPARATOR_UNPROVISIONED') from None


def _checked_cover_source(request, output):
    source=output.parent.parent/'source.wav'
    try:
        fd=os.open(source,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK)
        with os.fdopen(fd,'rb') as stream:
            st=os.fstat(stream.fileno())
            if (source.resolve(strict=True)!=source or not stat.S_ISREG(st.st_mode) or st.st_uid!=os.getuid()
                    or st.st_nlink!=1 or st.st_mode&0o077 or not 44<=st.st_size<=24*1024**2):
                raise ValueError
            data=stream.read(st.st_size+1)
        if len(data)!=st.st_size or sha256(data)!=request[COVER_SOURCE_KEY]:
            raise ValueError
        import io
        with wave.open(io.BytesIO(data),'rb') as wav:
            rate,channels,frames=wav.getframerate(),wav.getnchannels(),wav.getnframes()
            raw=wav.readframes(frames)
            if (wav.getsampwidth()!=2 or wav.getcomptype()!='NONE' or channels not in (1,2)
                    or not 8000<=rate<=96000 or frames/rate!=request['duration'] or len(raw)!=frames*channels*2):
                raise ValueError
        return raw,rate,channels
    except (OSError,ValueError,EOFError,wave.Error):
        raise ValueError('HQ_COVER_SOURCE_INVALID') from None


def _make_accompaniment(request, root, output, torch):
    weights,expected=_separator_checkpoint(root)
    try:
        from torchaudio.functional import resample
        from torchaudio.models import hdemucs_high
    except (ImportError,OSError):
        raise RuntimeError('HQ_SEPARATOR_UNPROVISIONED') from None
    tracks=['drums','bass','other','vocals']
    model=hdemucs_high(sources=tracks)
    fd=os.open(weights,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        st=os.fstat(stream.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.getuid() or st.st_nlink!=1 or st.st_mode&0o077:
            raise RuntimeError('HQ_SEPARATOR_UNPROVISIONED')
        if hashlib.file_digest(stream,'sha256').hexdigest()!=expected:
            raise RuntimeError('HQ_SEPARATOR_UNPROVISIONED')
        stream.seek(0)
        try:
            model.load_state_dict(torch.load(stream,map_location='cpu',weights_only=True),strict=True)
        except (ValueError,RuntimeError,EOFError,TypeError):
            raise RuntimeError('HQ_SEPARATOR_UNPROVISIONED') from None
    model.eval()
    raw,rate,channels=_checked_cover_source(request,output)
    samples=torch.frombuffer(bytearray(raw),dtype=torch.int16).reshape(-1,channels).T.float()/32768.
    if channels==1:samples=samples.repeat(2,1)
    if rate!=44100:samples=resample(samples,rate,44100)
    ref=samples.mean(0); ref_std=ref.std()
    if not torch.isfinite(ref_std) or ref_std<1e-5:
        raise RuntimeError('HQ_COVER_ACCOMPANIMENT_EMPTY')
    normalized=(samples-ref.mean())/ref_std
    # HDemucs runs in bounded CPU chunks. Crossfade only the overlapping
    # inference boundaries; no time-stretch, normalization or trimming of song form.
    segment,overlap=10*44100,44100
    mix=torch.zeros_like(samples); denom=torch.zeros_like(ref)
    with torch.inference_mode():
        for start in range(0,samples.shape[1],segment-overlap):
            end=min(start+segment,samples.shape[1]); actual=end-start
            chunk=normalized[:,start:end]
            if actual<2*overlap:chunk=torch.nn.functional.pad(chunk,(0,2*overlap-actual))
            stems=model(chunk.unsqueeze(0))[0]
            if tuple(stems.shape[:2])!=(4,2) or stems.shape[-1]<actual:
                raise RuntimeError('HQ_COVER_ACCOMPANIMENT_EMPTY')
            accompaniment=stems[:3,:,:actual].sum(0)*ref_std
            fade=torch.ones(actual)
            if start:fade[:min(overlap,actual)]=torch.linspace(0,1,min(overlap,actual))
            if end<samples.shape[1]:fade[-overlap:]=torch.linspace(1,0,overlap)
            mix[:,start:end]+=accompaniment*fade
            denom[start:end]+=fade
            if end>=samples.shape[1]:break
    if not torch.all(denom>0):raise RuntimeError('HQ_COVER_ACCOMPANIMENT_EMPTY')
    mix=mix/denom
    if not torch.isfinite(mix).all() or mix.abs().mean()<1e-5:
        raise RuntimeError('HQ_COVER_ACCOMPANIMENT_EMPTY')
    peak=mix.abs().max()
    if peak>.98:mix=mix*(.98/peak)
    target=output.parent/'accompaniment.wav'
    fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as raw_out:
        with wave.open(raw_out,'wb') as audio:
            audio.setparams((2,2,44100,0,'NONE','not compressed'))
            audio.writeframes((mix.T*32767).round().to(torch.int16).contiguous().numpy().tobytes())
        raw_out.flush();os.fsync(raw_out.fileno())
    return target,sha256(target.read_bytes())


def _cover_params(request, accompaniment):
    return dict(task_type='cover',src_audio=str(accompaniment),reference_audio=None,
                audio_cover_strength=COVER_STRENGTH,cover_noise_strength=0.0,
                caption=COVER_CAPTION,lyrics='[Instrumental]',instrumental=True,
                duration=request['duration'],vocal_language='unknown')


def run_render(request, root, output, resident=None):
    cover=COVER_SOURCE_KEY in request
    if not cover:
        plan, plan_sha = read_plan(request, output)
    import torch
    from acestep.handler import AceStepHandler
    from acestep.inference import GenerationParams, GenerationConfig, generate_music
    if not torch.backends.mps.is_available():
        raise RuntimeError('HQ_MPS_REQUIRED')

    class LocalHandler(AceStepHandler):
        def _init_mlx_vae(self):
            self.use_mlx_vae = False
            return False

        def _load_main_model_from_checkpoint(self, **kwargs):
            from transformers import AutoModel
            original = AutoModel.from_pretrained
            def direct_load(*args, **load_kwargs):
                load_kwargs.update(device_map={'': 'mps'}, low_cpu_mem_usage=True,
                                   local_files_only=True)
                return original(*args, **load_kwargs)
            AutoModel.from_pretrained = direct_load
            try:
                return super()._load_main_model_from_checkpoint(**kwargs)
            finally:
                AutoModel.from_pretrained = original

        def _ensure_models_present(self, *, checkpoint_path, config_path, **kwargs):
            if config_path != DIT_MODEL or Path(checkpoint_path).resolve() != root / 'source/checkpoints':
                raise ValueError('HQ_RENDER_CHECKPOINT_MISMATCH')
            require_components(root / 'source/checkpoints')

    torch.set_num_threads(4)
    torch.manual_seed(request['seed']); random.seed(request['seed'])
    settings = dict(project_root=str(root / 'source'), config_path=DIT_MODEL,
                    device='mps', use_flash_attention=False, compile_model=False,
                    offload_to_cpu=False, offload_dit_to_cpu=False, quantization=None,
                    use_mlx_dit=False, vae_checkpoint='official')
    if cover:
        accompaniment,accompaniment_sha=_make_accompaniment(request,root,output,torch)
        conditioning=_cover_params(request,accompaniment)
    else:
        fields=musical_fields(request['settings'],plan['metadata'])
        fields.setdefault('vocal_language','unknown')
        conditioning=dict(audio_codes=plan['audio_codes'],caption=request['prompt'],
                          lyrics=_lyrics(request['settings']),instrumental=request['settings'].get('instrumental',True),
                          duration=plan['duration'],**fields)
    params = GenerationParams(**conditioning, seed=request['seed'], inference_steps=50, guidance_scale=7.0, shift=3.0,
                              thinking=False, use_cot_metas=False, use_cot_caption=False,
                              use_cot_language=False, enable_normalization=True, normalization_db=-1.0)
    config = GenerationConfig(batch_size=1, use_random_seed=False,
                              seeds=[request['seed']], audio_format='wav')
    lineage=({'coverSourceSha256':request[COVER_SOURCE_KEY],'accompanimentSha256':accompaniment_sha}
             if cover else {'lmResultSha256':plan_sha})
    write_json_new(output.parent / 'generation-settings.json',
                   {'requestSha256': request['request_sha256'], **lineage,
                    'init': settings, 'params': asdict(params), 'config': asdict(config)})
    handler = resident.get('renderer') if resident is not None else None
    started = time.monotonic()
    if handler is None:
        handler = LocalHandler()
        status, ok = handler.initialize_service(**settings)
        if not ok or handler.dtype != torch.float32 or handler.use_mlx_dit or handler.use_mlx_vae:
            raise RuntimeError('HQ_MPS_FP32_RENDERER_REQUIRED')
        if resident is not None:
            resident['renderer'] = handler
    generated_root = output.parent / 'ace-output'
    generated_root.mkdir(exist_ok=False)
    result = generate_music(handler, None, params, config, save_dir=str(generated_root))
    if not result.success or len(result.audios) != 1:
        raise RuntimeError('HQ_RENDER_GENERATION_FAILED')
    generated = Path(result.audios[0]['path'])
    if not generated.is_absolute():
        generated = generated_root / generated
    if generated.resolve() != generated or not generated.is_relative_to(generated_root) or not generated.is_file() or generated.stat().st_nlink != 1:
        raise ValueError('HQ_GENERATED_OUTPUT_PATH_INVALID')
    duration = wav_details(generated, conditioning['duration'])
    # Copy into a new output only. Preserve the runtime's generated file and
    # never remove/overwrite unrelated inputs or any earlier job's output.
    with generated.open('rb') as source, output.open('xb') as dest:
        shutil.copyfileobj(source, dest)
        dest.flush(); os.fsync(dest.fileno())
    output_bytes = output.read_bytes()
    write_json_new(output.parent / 'render-result.json',
                   {'schemaVersion': 'ace-hq-cover-result.v1' if cover else 'ace-hq-render.v1', 'success': True,
                    'profileVersion': PROFILE_VERSION, 'requestSha256': request['request_sha256'],
                    'model': DIT_MODEL, 'modelRevision': DIT_REVISION,
                    'runtimeRevision': RUNTIME_REVISION, 'backend': 'mps', 'dtype': 'float32',
                    **lineage, 'audioSha256': sha256(output_bytes),
                    'audioBytes': len(output_bytes), 'sampleRate': 48000, 'channels': 2,
                    'durationSeconds': duration, 'seed': request['seed'],
                    'elapsedSeconds': time.monotonic() - started})


def resident_loop(stage, root, parent_pid, fd):
    watch_parent(parent_pid)
    require_components(root / 'source/checkpoints')
    offline_environment(root)
    sys.path.insert(0, str(root / 'source'))
    cache = {}
    execute = run_plan if stage == 'plan' else run_render
    with os.fdopen(fd, 'wb', buffering=0) as responses:
        while True:
            raw = sys.stdin.buffer.readline(4097)
            if not raw:
                return
            if len(raw)>4096 or not raw.endswith(b'\n'):
                raise ValueError('HQ_RESIDENT_REQUEST_FRAME')
            msg = json.loads(raw, object_pairs_hook=_object)
            if type(msg) is not dict or set(msg) != {'path','requestSha256'}:
                raise ValueError('HQ_RESIDENT_REQUEST_FRAME')
            request_path = Path(msg['path'])
            request, _ = load_json(request_path, 128 * 1024)
            job_root, output = validate_request(request, request_path)
            if job_root != root or request['parent_pid'] != parent_pid or request['request_sha256'] != msg['requestSha256']:
                raise ValueError('HQ_RESIDENT_JOB_BINDING')
            try:
                execute(request, root, output, cache)
            except Exception as exc:
                # Only a shaped token is returned; backend allowlists it.
                token = str(exc).split(':',1)[0]
                code = token if re.fullmatch(r'HQ_[A-Z0-9_]{1,58}',token) else 'HQ_CHILD_NONZERO_EXIT'
                if ('MPS backend out of memory' in str(exc) or "DefaultCPUAllocator: can't allocate memory" in str(exc)):
                    code = 'HQ_CHILD_OUT_OF_MEMORY'
                responses.write(canonical({'requestSha256':request['request_sha256'],'ok':False,'code':code})+b'\n')
                return
            responses.write(canonical({'requestSha256':request['request_sha256'],'ok':True})+b'\n')


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args)==5 and args[0]=='--resident' and args[1] in ('plan','render'):
        return resident_loop(args[1],Path(args[2]),int(args[3]),int(args[4]))
    if len(args) != 2 or args[0] not in ('plan', 'render'):
        raise ValueError('USAGE: ace_hq_runner.py plan|render REQUEST_JSON')
    request_path = Path(args[1])
    request, _ = load_json(request_path, 128 * 1024)
    root, output = validate_request(request, request_path)
    require_components(root / 'source/checkpoints')
    offline_environment(root)
    watch_parent(request['parent_pid'])
    sys.path.insert(0, str(root / 'source'))
    if args[0] == 'plan':
        run_plan(request, root, output)
    else:
        run_render(request, root, output)


if __name__ == '__main__':
    main()
