"""Draft portable manifests only. Store paths, publication and authority live in PDS.

Raw native sequences are retained independently; neither output grants deletion.
Callers supply verified bytes, never producer-local paths across the network.
"""
import copy
import hashlib
import io
import json
from .animation_native_png import BINDING_KEYS, DIRECTIONS, SHA256, _size, validate_native_png_manifest


def manifest_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _hash(value):
    if not isinstance(value, str) or not SHA256.fullmatch(value):
        raise ValueError('DURABLE_FRAME_HASH_REQUIRED')
    return value


def _provenance(value):
    # Explicit allowlist excludes model paths, arbitrary URLs, tokens and scratch.
    keys = {'engine', 'modelId', 'modelDigest', 'policyHash', 'inferenceRequestId', 'fixture'}
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError('DURABLE_FRAME_PROVENANCE_REQUIRED')
    from .animation_native_png import IDENTIFIER
    if any(not isinstance(value[k], str) or not IDENTIFIER.fullmatch(value[k]) for k in ('engine', 'modelId', 'inferenceRequestId')):
        raise ValueError('DURABLE_FRAME_PROVENANCE_INVALID')
    if type(value['fixture']) is not bool: raise ValueError('DURABLE_FRAME_FIXTURE_REQUIRED')
    _hash(value['modelDigest']); _hash(value['policyHash'])
    return dict(value)


def build_alpha_manifest(raw_manifest, raw_payloads, alpha_payloads, *, dependency_hash, inference, raw_manifest_id, alpha_request_id):
    from PIL import Image
    from .animation_native_png import IDENTIFIER
    if not isinstance(raw_manifest_id, str) or not raw_manifest_id or not isinstance(alpha_request_id, str) or not IDENTIFIER.fullmatch(alpha_request_id):
        raise ValueError('DURABLE_ALPHA_REFERENCE_REQUIRED')
    binding = {k: raw_manifest[k] for k in BINDING_KEYS}
    raw = validate_native_png_manifest(raw_manifest, binding, raw_payloads,
        frame_count=raw_manifest['frameCount'], fps=raw_manifest['fps'])
    if len(alpha_payloads) != raw['frameCount']:
        raise ValueError('DURABLE_ALPHA_COUNT_INVALID')
    entries = []
    for i, payload in enumerate(alpha_payloads):
        if _size(payload) != (raw['width'], raw['height']):
            raise ValueError('DURABLE_ALPHA_CANVAS_CHANGED')
        with Image.open(io.BytesIO(payload)) as im:
            if im.mode != 'RGBA': raise ValueError('DURABLE_ALPHA_RGBA_REQUIRED')
        sha = hashlib.sha256(payload).hexdigest()
        entries.append({'index': i, 'rawAssetId': raw['frames'][i]['assetId'],
            'alphaAssetId': sha+'.png', 'sha256': sha, 'bytes': len(payload), 'mimeType': 'image/png'})
    return {'schemaVersion': 'ngs.animation-alpha-output.v1', **binding,
        'requestId': alpha_request_id, 'rawRequestId': raw['requestId'], 'rawManifestId': raw_manifest_id,
        'dependencyHash': _hash(dependency_hash), 'rawManifestHash': manifest_hash(raw),
        'frameCount': raw['frameCount'], 'fps': raw['fps'],
        'width': raw['width'], 'height': raw['height'],
        'frames': entries, 'inferenceProvenance': _provenance(inference),
        'rawRetention': 'retain-native-sequence-independently'}


def alpha_dag_candidate(alpha):
    """Existing internal DAG shape; no claim that a Filex commit occurred."""
    return {'schemaVersion': 'ngs.animation-alpha-output-candidate.v1',
        'dependencyHash': alpha['dependencyHash'],
        'assetIds': [f['alphaAssetId'] for f in alpha['frames']]}


def build_pixel_manifest(processed, alphas):
    """Wrap existing process_set maps, stripping its local artifacts path map.

    This is an uncommitted candidate. Request/worker authority validates pixel
    bytes and dependency manifests/commit receipts. PDS only forwards generic
    authenticated storage; this builder grants no publication or completion.
    """
    if set(alphas) != set(DIRECTIONS) or set(processed['directions']) != set(DIRECTIONS):
        raise ValueError('DURABLE_PIXEL_EIGHT_DIRECTIONS_REQUIRED')
    if processed['canvas']['scale'] != .5 or processed['pixelPolicy']['colors'] != 32 or processed['pixelPolicy']['paletteScope'] != 'all-directions-all-frames':
        raise ValueError('DURABLE_PIXEL_SHARED_POLICY_REQUIRED')
    output = {'schemaVersion': 'ngs.animation-pixel-set-candidate.v1',
        'rawRetention': 'retain-native-sequence-independently',
        'canvas': copy.deepcopy(processed['canvas']), 'pixelPolicy': copy.deepcopy(processed['pixelPolicy']),
        'directions': {}}
    cohort = None
    for d in DIRECTIONS:
        alpha = alphas[d]; row = processed['directions'][d]
        identity = tuple(alpha[k] for k in ('npcId','generationId','posePreset','presetId'))
        if alpha['direction'] != d or (cohort is not None and identity != cohort):
            raise ValueError('DURABLE_PIXEL_COHORT_CHANGED')
        cohort = identity
        if row['frameCount'] != alpha['frameCount'] or row['fps'] != alpha['fps'] or len(row['frames']) != alpha['frameCount']:
            raise ValueError('DURABLE_PIXEL_TIMING_CHANGED')
        frames=[]
        for i, (f,a) in enumerate(zip(row['frames'], alpha['frames'])):
            if f['index'] != i or a['index'] != i or any(f[k] != a[k] for k in ('rawAssetId','alphaAssetId')) or f['sourceTimestamp'] != i/alpha['fps']:
                raise ValueError('DURABLE_PIXEL_LINEAGE_CHANGED')
            # Whitelist the legacy portable frame fields; never copy local trace.
            frames.append({k:copy.deepcopy(f[k]) for k in ('index','sourceTimestamp','assetId','rawAssetId','alphaAssetId','sourceBounds')})
        output['directions'][d] = {'alphaManifestHash': manifest_hash(alpha),
            'rawManifestHash': alpha['rawManifestHash'], 'sourceBindingHash': alpha['sourceBindingHash'],
            'frameCount': row['frameCount'], 'fps': row['fps'], 'frames': frames}
    output.update(dict(zip(('npcId','generationId','posePreset','presetId'),cohort)))
    return output
