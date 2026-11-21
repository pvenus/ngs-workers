"""Isolated ACE-Step entry point. Heavy imports occur only during a real job."""
import json
import os
import signal
import threading
import time
import shutil
import sys
from dataclasses import asdict
from pathlib import Path


def main():
    request = json.loads(Path(sys.argv[1]).read_text())
    def watch_parent():
        while True:
            if os.getppid() != request['parent_pid']:
                os.killpg(os.getpgrp(), signal.SIGTERM)
                return
            time.sleep(1)
    threading.Thread(target=watch_parent, daemon=True).start()
    root = Path(request['runtime_root'])
    sys.path.insert(0, str(root / 'source'))
    import torch
    from acestep.handler import AceStepHandler
    from acestep.inference import GenerationParams, GenerationConfig, generate_music

    class LocalHandler(AceStepHandler):
        def _ensure_models_present(self, *, checkpoint_path, config_path, **kwargs):
            if config_path != 'acestep-v15-turbo':
                raise ValueError('unsupported ACE profile')
            required = ['acestep-v15-turbo/model.safetensors',
                        'acestep-v15-turbo/config.json',
                        'acestep-v15-turbo/silence_latent.pt',
                        'Qwen3-Embedding-0.6B/model.safetensors',
                        'Qwen3-Embedding-0.6B/tokenizer.json',
                        'vae/diffusion_pytorch_model.safetensors', 'vae/config.json']
            for relative in required:
                path = Path(checkpoint_path) / relative
                if not path.is_file() or not path.stat().st_size:
                    raise FileNotFoundError(path)
            return None

    torch.set_num_threads(4)
    handler = LocalHandler()
    settings = dict(project_root=str(root / 'source'), config_path='acestep-v15-turbo',
                    device='mps', use_flash_attention=False, compile_model=False,
                    offload_to_cpu=True, offload_dit_to_cpu=True, quantization=None,
                    use_mlx_dit=True, vae_checkpoint='official')
    params = GenerationParams(caption=request['prompt'], lyrics='[Instrumental]',
        instrumental=True, bpm=request['settings'].get('bpm'), duration=request['duration'],
        inference_steps=8, seed=request['seed'], guidance_scale=1.0, thinking=False,
        use_cot_metas=False, use_cot_caption=False, use_cot_language=False,
        enable_normalization=True, normalization_db=-1.0)
    config = GenerationConfig(batch_size=1, use_random_seed=False,
                              seeds=[request['seed']], audio_format='wav')
    output = Path(request['output'])
    (output.parent / 'ace-settings.json').write_text(json.dumps(
        {'init': settings, 'params': asdict(params), 'config': asdict(config)}, indent=2))
    status, ok = handler.initialize_service(**settings)
    if not ok:
        raise RuntimeError(status)
    result = generate_music(handler, None, params, config,
                            save_dir=str(output.parent / 'ace-output'))
    if not result.success or len(result.audios) != 1:
        raise RuntimeError(result.error or 'ACE generation failed')
    generated = Path(result.audios[0]['path']).resolve()
    if not generated.is_relative_to((output.parent / 'ace-output').resolve()):
        raise ValueError('unexpected generated audio path')
    shutil.move(str(generated), output)


if __name__ == '__main__':
    main()
