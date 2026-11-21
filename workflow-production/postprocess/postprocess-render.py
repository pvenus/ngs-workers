"""Workflow per-direction half-size pixel output from exact native PNGs."""
import hashlib, json, os, sys
from pathlib import Path

def sha(data): return hashlib.sha256(data).hexdigest()

def wire_numbers(value):
    # Node only preserves JSON's numeric values, not a float's integral spelling.
    # Hash the same geometry that the receiving PDS/JSON parser will see.
    if type(value) is float and value.is_integer(): return int(value)
    if type(value) is list: return [wire_numbers(v) for v in value]
    if type(value) is dict: return {k: wire_numbers(v) for k,v in value.items()}
    return value

def process(root, claim, motion, direction, raw, model_digest, alpha_runner):
    from workers.resident.backends.animation_native_png import BINDING_KEYS, validate_native_png_manifest
    from workers.resident.backends.frame_native_manifests import build_alpha_manifest, manifest_hash
    from workers.resident.backends.frame_processing import process_set
    paths = sorted((root/'raw').glob('frame-*.png'))
    original = [p.read_bytes() for p in paths]
    values = {k: raw[k] for k in BINDING_KEYS}
    if (raw['npcId'], raw['posePreset'], raw['presetId'], raw['direction']) != (claim['npcId'], 'idle', motion, direction):
        raise ValueError('POSTPROCESS_SOURCE_SCOPE')
    validate_native_png_manifest(raw, values, original, frame_count=raw['frameCount'], fps=raw['fps'])
    raw_path = f"canonical-files/binari/npcs/{claim['npcId']}/animation-sequences/idle/{motion}/{direction}.json"
    policy = dict(scale=.5, colors=32, threshold=128, scope='all-frames-in-direction',
                  engine='birefnet-sam2', temporalStrength=0.0)
    alpha_runner(root, model_digest, {'sourceSha256': manifest_hash(raw)})
    alpha_paths = sorted((root/'alpha').glob('frame-*.png'))
    mattes = [p.read_bytes() for p in alpha_paths]
    if [p.read_bytes() for p in paths] != original:
        raise ValueError('POSTPROCESS_RAW_CHANGED')
    aid = claim['taskId']
    alpha = build_alpha_manifest(raw, original, mattes, dependency_hash=manifest_hash(dict(claim=claim, rawManifestHash=manifest_hash(raw))),
                inference=dict(engine='birefnet-sam2', modelId='birefnet-matting.sam2.1-small', modelDigest=model_digest,
                               policyHash=manifest_hash(policy), inferenceRequestId=aid, fixture=False),
                raw_manifest_id=raw_path, alpha_request_id=aid)
    result = process_set({direction: dict(rawFrames=paths, alphaFrames=alpha_paths, fps=raw['fps'],
                         sourceAssetId=raw_path)}, root/'assets', pixel_scale=.5)
    pixel_policy = dict(result['pixelPolicy'], paletteScope='all-frames-in-direction')
    canvas = wire_numbers(result['canvas'])
    pixel = dict(schemaVersion='workflow.animation-pixel-direction.v1', requestId=aid, npcId=raw['npcId'],
                 generationId=raw['generationId'], posePreset='idle', presetId=motion, direction=direction,
                 rawRetention='retain-native-sequence-independently', canvas=canvas, pixelPolicy=pixel_policy,
                 frames=result['directions'][direction]['frames'], rawManifestHash=manifest_hash(raw),
                 alphaManifestHash=manifest_hash(alpha), sourceBindingHash=raw['sourceBindingHash'],
                 normalizationHash=manifest_hash(dict(canvas=canvas,pixelPolicy=pixel_policy)),
                 frameCount=raw['frameCount'], fps=raw['fps'])
    return dict(alpha=alpha,pixel=pixel)

if __name__=='__main__':
    from workers.resident.backends.frame_full_alpha_cli import run_alpha
    import re
    model_digest=os.environ['WORKFLOW_POSTPROCESS_MODEL_DIGEST']
    if not re.fullmatch('[a-f0-9]{64}',model_digest): raise ValueError('POSTPROCESS_MODEL_DIGEST_REQUIRED')
    root = Path(sys.argv[1]); data = json.loads((root/'input.json').read_text())
    out = process(root, data['claim'], data['motion'], data['direction'], data['sequence'],model_digest,run_alpha)
    (root/'output.json').write_text(json.dumps(out,allow_nan=False))
