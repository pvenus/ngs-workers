#!/usr/bin/env python3
"""Provision a NEW local Workers workspace, without installing or starting anything."""
import argparse
import json
import os
from pathlib import Path
import shlex
import sys

SOURCE = Path(__file__).resolve().parents[1]


def new_root(value):
    root = Path(value).expanduser()
    if (not root.is_absolute() or '..' in root.parts
            or any(ord(c) < 32 for c in str(root))):
        raise ValueError('Use a canonical absolute workspace path')
    if root.exists() or root.is_symlink():
        raise ValueError('Workspace already exists; this is not a migration/overwrite tool')
    parent = root.parent
    if not parent.is_dir() or parent.resolve(strict=True) != parent:
        raise ValueError('Workspace parent must already exist and must not contain symlinks')
    info = parent.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise ValueError('Workspace parent must be owned by you and not group/world writable')
    if root == SOURCE or SOURCE in root.parents or root in SOURCE.parents:
        raise ValueError('Workspace must not overlap this source checkout')
    return root


def layout(root):
    directories = {
        'config': 0o700, 'runtime': 0o700, 'data': 0o700, 'logs': 0o700,
        'data/audioforge': 0o700, 'data/audioforge/artifacts': 0o700,
        'data/audioforge/bgm-requests': 0o700, 'data/audioforge/bgm-request-state': 0o700,
        'data/audioforge/reviews': 0o700, 'data/imageforge': 0o700,
        'data/imageforge/resources': 0o755, 'data/imageforge/resources/v1': 0o755,
        'data/imageforge/resources/v1/imageforge': 0o700,
        'data/imageforge/legacy': 0o700,
    }
    audio = root / 'data/audioforge'
    image = root / 'data/imageforge'
    audio_env = {
        'WORKERS_AUDIO_VENV': str(root / 'runtime/audioforge-service/.venv'),
        'WORKERS_DATA_ROOT': str(audio),
        'ACE_RUNTIME_ROOT': str(root / 'runtime/acestep'),
        'AUDIOFORGE_DATA_DIR': str(audio),
        'AUDIOFORGE_DB_PATH': str(audio / 'queue.sqlite3'),
        'AUDIOFORGE_ARTIFACT_DIR': str(audio / 'artifacts'),
        'AUDIOFORGE_ACESTEP_ROOT': str(root / 'runtime/acestep'),
        'AUDIOFORGE_API_URL': 'http://127.0.0.1:8765',
        'AUDIOFORGE_GATEWAY_URL': 'http://127.0.0.1:8767',
        'AUDIOFORGE_BACKEND': 'ace-step',
        'AUDIOFORGE_MODEL': 'acestep-v15-turbo',
    }
    image_env = {
        'WORKERS_IMAGE_VENV': str(root / 'runtime/imageforge-service/.venv'),
        'IMAGEFORGE_STORAGE_MODE': 'internal',
        'IMAGEFORGE_STORAGE_BASE': str(image),
        'IMAGEFORGE_RESOURCE_ROOT': str(image / 'resources/v1'),
        'IMAGEFORGE_LEGACY_ROOT': str(image / 'legacy'),
        'IMAGEFORGE_LEGACY_INVENTORY': str(image / 'legacy/legacy_inventory.json'),
        'IMAGEFORGE_HOST': '127.0.0.1',
        'IMAGEFORGE_PORT': '8000',
    }
    return directories, audio_env, image_env


def env_text(values):
    return ('# New-machine paths only. No credentials, licenses or service activation.\n'
            '# Model/runtime and service venv paths must be provisioned separately.\n'
            + ''.join(f'export {key}={shlex.quote(value)}\n' for key, value in values.items()))


def write_new(path, content):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(content)


def initialize(root, apply=False):
    root = new_root(root)
    directories, audio, image = layout(root)
    result = {
        'schemaVersion': 'workers.workspace.v1',
        'state': 'created-not-activated' if apply else 'plan-only',
        'workspace': str(root),
        'directories': {name: oct(mode) for name, mode in directories.items()},
        'environmentFiles': ['config/audioforge.env', 'config/imageforge.env'],
        'installsDependencies': False, 'createsDatabase': False,
        'downloadsModels': False, 'startsServices': False, 'enablesConsumer': False,
        'readyForGeneration': False,
        'nextSteps': ['install service venvs', 'provision approved model runtimes and identity/data',
                      'check source/config', 'explicit service/queue ownership handover'],
    }
    if not apply:
        return result
    # Exclusive creation: an existing workspace is never merged, repaired or reset.
    # On failure preserve the partial directory for inspection rather than deleting data.
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    for name, mode in directories.items():
        path = root / name
        path.mkdir(mode=mode)
        path.chmod(mode)  # Required ImageForge namespace modes, independent of umask.
    write_new(root / 'config/audioforge.env', env_text(audio))
    write_new(root / 'config/imageforge.env', env_text(image))
    # Empty means this NEW machine has no inherited private legacy resources.
    write_new(root / 'data/imageforge/legacy/legacy_inventory.json', '{}\n')
    write_new(root / 'config/workspace.json', json.dumps(result, indent=2) + '\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, help='new absolute directory; its parent must exist')
    parser.add_argument('--apply', action='store_true', help='create the new layout; default only prints a plan')
    args = parser.parse_args()
    try:
        result = initialize(args.root, args.apply)
    except (OSError, ValueError) as exc:
        print(json.dumps({'state': 'blocked', 'error': str(exc)}))
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
