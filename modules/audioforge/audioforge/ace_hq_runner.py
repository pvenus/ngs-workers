"""Two isolated HQ child stages; importing this module never loads a model.

CLI: python ace_hq_runner.py plan|render REQUEST_JSON
The parent owns admission, runtime/hash validation, locks, resource guards and
waiting for the planning process to exit before starting the renderer.
"""
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
import sys
import threading
import time
import wave

PROFILE_VERSION = 'ace-xl-sft-lm4b.v1'
RUNTIME_REVISION = 'ca1e85fe9430179831e6bc6be790c332190a3866'
LM_MODEL = 'acestep-5Hz-lm-4B'
LM_REVISION = '0a3ec94b557aea7d508da38b31cfe7341f6ff737'
DIT_MODEL = 'acestep-v15-xl-sft'
DIT_REVISION = 'd06de46b4622f781cf07f4a013a67d591ca52819'
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
    if type(duration) not in (int, float) or not math.isfinite(duration) or not 10 <= duration <= 30:
        raise ValueError('HQ_DURATION_INVALID')
    _text(value.get('prompt'), 2000, empty=False)
    settings = value.get('settings')
    if not isinstance(settings, dict) or set(settings) - {'instrumental', 'lyrics', 'bpm', 'keyscale', 'timesignature', 'vocal_language'}:
        raise ValueError('HQ_SETTINGS_INVALID')
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


def run_plan(request, root, output):
    import torch
    import numpy as np
    import mlx.core as mx
    from acestep.llm_inference import LLMHandler
    if not torch.backends.mps.is_available():
        raise RuntimeError('HQ_MPS_REQUIRED')
    lm = LLMHandler()
    started = time.monotonic()
    try:
        status, ok = lm.initialize(checkpoint_dir=str(root / 'source/checkpoints'),
                                  lm_model_path=LM_MODEL, backend='mlx', device='mps',
                                  offload_to_cpu=False, dtype=torch.bfloat16)
        if not ok or lm.llm_backend != 'mlx':
            raise RuntimeError('HQ_LM_MLX_INITIALIZATION_REQUIRED')
        random.seed(request['seed']); np.random.seed(request['seed'])
        torch.manual_seed(request['seed']); mx.random.seed(request['seed'])
        options = dict(caption=request['prompt'], lyrics=_lyrics(request['settings']),
                       infer_type='llm_dit', temperature=.85, cfg_scale=2.0,
                       negative_prompt='NO USER INPUT', top_k=None, top_p=.9,
                       target_duration=request['duration'],
                       user_metadata={'duration': request['duration'], **musical_fields(request['settings'])},
                       use_cot_metas=False, use_cot_caption=False, use_cot_language=False,
                       use_constrained_decoding=True, batch_size=1, seeds=[request['seed']])
        write_json_new(output.parent / 'lm-options.json', options)
        raw = lm.generate_with_stop_condition(**options)
        if not isinstance(raw, dict) or raw.get('success') is not True:
            raise RuntimeError('HQ_LM_GENERATION_FAILED')
        codes = validate_codes(raw.get('audio_codes'))
        metadata = raw.get('metadata')
        if not isinstance(metadata, dict) or len(canonical(metadata)) > 16384:
            raise ValueError('HQ_LM_METADATA_INVALID')
        musical_fields(request['settings'], metadata)
        # Preserve the returned metadata exactly; only validated scalar values
        # are eligible to influence render parameters.
        envelope = dict(schemaVersion='ace-hq-plan.v1', success=True,
                        profileVersion=PROFILE_VERSION, requestSha256=request['request_sha256'],
                        model=LM_MODEL, modelRevision=LM_REVISION, runtimeRevision=RUNTIME_REVISION,
                        backend='mlx', dtype='bfloat16', plannerPid=os.getpid(),
                        seed=request['seed'], duration=request['duration'],
                        audio_codes=codes, metadata=metadata,
                        elapsedSeconds=time.monotonic() - started)
        # No default=str coercion: unexpected runtime objects must not masquerade
        # as valid replayable model output.
        write_json_new(output.parent / 'raw-model-output.json', raw)
    finally:
        lm.unload()
    write_json_new(output.parent / 'lm-result.json', envelope)


def read_plan(request, output):
    plan, raw = load_json(output.parent / 'lm-result.json')
    expected = {'schemaVersion': 'ace-hq-plan.v1', 'success': True,
                'profileVersion': PROFILE_VERSION, 'requestSha256': request['request_sha256'],
                'model': LM_MODEL, 'modelRevision': LM_REVISION, 'runtimeRevision': RUNTIME_REVISION,
                'backend': 'mlx', 'dtype': 'bfloat16', 'seed': request['seed'], 'duration': request['duration']}
    for key, value in expected.items():
        if type(plan.get(key)) is not type(value) or plan[key] != value:
            raise ValueError('HQ_PLANNER_BINDING_MISMATCH:' + key)
    validate_codes(plan.get('audio_codes'))
    if not isinstance(plan.get('metadata'), dict) or len(canonical(plan['metadata'])) > 16384:
        raise ValueError('HQ_LM_METADATA_INVALID')
    musical_fields(request['settings'], plan['metadata'])
    return plan, sha256(raw)


def wav_details(path, expected_duration):
    with wave.open(str(path), 'rb') as wav:
        duration = wav.getnframes() / wav.getframerate()
        if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth(), wav.getcomptype()) != (48000, 2, 2, 'NONE'):
            raise ValueError('HQ_WAV_FORMAT_INVALID')
        if abs(duration - expected_duration) > .1:
            raise ValueError('HQ_WAV_DURATION_INVALID')
    return duration


def run_render(request, root, output):
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
    fields = musical_fields(request['settings'], plan['metadata'])
    fields.setdefault('vocal_language', 'unknown')
    params = GenerationParams(audio_codes=plan['audio_codes'], caption=request['prompt'],
                              lyrics=_lyrics(request['settings']),
                              instrumental=request['settings'].get('instrumental', True),
                              duration=request['duration'], seed=request['seed'],
                              inference_steps=50, guidance_scale=7.0, shift=3.0,
                              thinking=False, use_cot_metas=False, use_cot_caption=False,
                              use_cot_language=False, enable_normalization=True,
                              normalization_db=-1.0, **fields)
    config = GenerationConfig(batch_size=1, use_random_seed=False,
                              seeds=[request['seed']], audio_format='wav')
    write_json_new(output.parent / 'generation-settings.json',
                   {'requestSha256': request['request_sha256'], 'lmResultSha256': plan_sha,
                    'init': settings, 'params': asdict(params), 'config': asdict(config)})
    handler = LocalHandler()
    started = time.monotonic()
    status, ok = handler.initialize_service(**settings)
    if not ok or handler.dtype != torch.float32 or handler.use_mlx_dit or handler.use_mlx_vae:
        raise RuntimeError('HQ_MPS_FP32_RENDERER_REQUIRED')
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
    duration = wav_details(generated, request['duration'])
    # Copy into a new output only. Preserve the runtime's generated file and
    # never remove/overwrite unrelated inputs or any earlier job's output.
    with generated.open('rb') as source, output.open('xb') as dest:
        shutil.copyfileobj(source, dest)
        dest.flush(); os.fsync(dest.fileno())
    output_bytes = output.read_bytes()
    write_json_new(output.parent / 'render-result.json',
                   {'schemaVersion': 'ace-hq-render.v1', 'success': True,
                    'profileVersion': PROFILE_VERSION, 'requestSha256': request['request_sha256'],
                    'model': DIT_MODEL, 'modelRevision': DIT_REVISION,
                    'runtimeRevision': RUNTIME_REVISION, 'backend': 'mps', 'dtype': 'float32',
                    'lmResultSha256': plan_sha, 'audioSha256': sha256(output_bytes),
                    'audioBytes': len(output_bytes), 'sampleRate': 48000, 'channels': 2,
                    'durationSeconds': duration, 'seed': request['seed'],
                    'elapsedSeconds': time.monotonic() - started})


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
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
