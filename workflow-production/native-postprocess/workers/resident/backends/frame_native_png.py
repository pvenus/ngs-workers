"""Consumer-local native PNG preparation; transport and alpha model are injected.

No producer paths, video extraction, publication, or lifecycle ownership here.
The caller owns the attempt directory and cleans it only after durable receipt.
"""
import hashlib
from pathlib import Path
from .animation_native_png import validate_native_png_manifest, DIRECTIONS


def prepare_direction(attempt, sequence_ref, expected, read_sequence, read_asset, alpha_prepare):
    """Read opaque durable reference, verify bytes, then prepare alpha for one direction.

    expected is separately trusted binding/frameCount/fps from the admitted claim.
    read_sequence/read_asset are authenticated PDS transport callbacks, not paths.
    alpha_prepare(raw_paths, alpha_directory) returns ordered local RGBA PNG paths.
    """
    root = Path(attempt)
    if root.is_symlink() or not root.is_dir():
        raise ValueError('NATIVE_FRAME_ATTEMPT_REQUIRED')
    root = root.resolve(strict=True)
    manifest = read_sequence(sequence_ref)
    binding = expected['binding']
    # Bound cardinality before fetching remote assets.
    count = expected['frameCount']
    if type(count) is not int or not 2 <= count <= 1000:
        raise ValueError('NATIVE_FRAME_COUNT_INVALID')
    if not isinstance(manifest, dict) or len(manifest.get('frames', [])) != count:
        raise ValueError('NATIVE_FRAME_COUNT_INVALID')
    # Never pass unverified producer-provided paths/URLs to a transport callback.
    import re
    entries = manifest['frames']
    if any(not isinstance(f, dict) or not re.fullmatch(r'[a-f0-9]{64}\.png', str(f.get('assetId', ''))) for f in entries):
        raise ValueError('NATIVE_FRAME_ASSET_INVALID')
    payloads = [read_asset(f['assetId']) for f in entries]
    validated = validate_native_png_manifest(manifest, binding, payloads,
        frame_count=count, fps=expected['fps'])
    folder = root / validated['direction']
    folder.mkdir(mode=0o700)  # Exclusive: never overwrite/reuse another attempt.
    raw = folder / 'raw'; raw.mkdir(mode=0o700)
    paths = []
    for i, data in enumerate(payloads):
        path = raw / ('%08d.png' % i)
        with path.open('xb') as stream:
            stream.write(data)
        paths.append(path)
    alpha = folder / 'alpha'; alpha.mkdir(mode=0o700)
    alpha_paths = list(alpha_prepare(tuple(paths), alpha))
    if len(alpha_paths) != count:
        raise ValueError('NATIVE_FRAME_ALPHA_COUNT_INVALID')
    from PIL import Image
    for i, path in enumerate(alpha_paths):
        path = Path(path)
        if path.is_symlink() or path.parent != alpha or not path.is_file():
            raise ValueError('NATIVE_FRAME_ALPHA_PATH_INVALID')
        with Image.open(path) as im:
            im.load()
            if im.format != 'PNG' or im.mode != 'RGBA' or im.size != (validated['width'], validated['height']):
                raise ValueError('NATIVE_FRAME_ALPHA_CANVAS_INVALID')
        if paths[i].is_symlink() or paths[i].read_bytes() != payloads[i]:
            raise ValueError('NATIVE_FRAME_RAW_CHANGED')
    return {'sourceAssetId': sequence_ref, 'frameCount': count, 'fps': validated['fps'],
        'rawFrames': [str(p) for p in paths], 'alphaFrames': [str(p) for p in alpha_paths],
        'frameTimes': [i / validated['fps'] for i in range(count)],
        'trace': {'inputMode': 'png-sequence-v1', 'sourceBinding': binding,
                  'rawSha256': [f['sha256'] for f in entries],
                  'alphaSha256': [hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in alpha_paths],
                  'extractionCalls': 0}}


def process_directions(directions, output):
    """Only the complete eight-direction cohort enters shared palette/geometry."""
    if set(directions) != set(DIRECTIONS):
        raise ValueError('NATIVE_FRAME_EIGHT_DIRECTIONS_REQUIRED')
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise ValueError('NATIVE_FRAME_OUTPUT_EXISTS')
    cohort = None
    for direction, row in directions.items():
        binding = row['trace']['sourceBinding']
        identity = tuple(binding[k] for k in ('npcId', 'generationId', 'posePreset', 'presetId'))
        if cohort is not None and identity != cohort:
            raise ValueError('NATIVE_FRAME_COHORT_CHANGED')
        cohort = identity
        if row['trace']['sourceBinding']['direction'] != direction:
            raise ValueError('NATIVE_FRAME_DIRECTION_CHANGED')
        for field, hashes in (('rawFrames', 'rawSha256'), ('alphaFrames', 'alphaSha256')):
            if len(row[field]) != row['frameCount'] or len(row['trace'][hashes]) != row['frameCount']:
                raise ValueError('NATIVE_FRAME_COUNT_CHANGED')
            for path, sha in zip(row[field], row['trace'][hashes]):
                p = Path(path)
                if p.is_symlink() or hashlib.sha256(p.read_bytes()).hexdigest() != sha:
                    raise ValueError('NATIVE_FRAME_BYTES_CHANGED')
    from .frame_processing import process_set
    # Existing policy owns all bounds, 10% margin, pivot, binary alpha and 32 colors.
    return process_set({d: directions[d] for d in DIRECTIONS}, output, pixel_scale=.5)
