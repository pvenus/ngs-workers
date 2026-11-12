#!/usr/bin/env python3
"""Run an existing AudioForge role from this exact unified Workers checkout."""
import argparse
import importlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / 'modules/audioforge'
ROLES = {'generation': 'api', 'catalog': 'gateway_service', 'review': 'review_api', 'file-worker': 'file_workflow'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('role', choices=ROLES)
    parser.add_argument('--interval', type=float, default=30, help='file-worker poll interval in seconds')
    parser.add_argument('--check', action='store_true', help='check source/config without opening a DB or starting a worker')
    args = parser.parse_args()
    if sys.version_info < (3, 11):
        parser.error('Python 3.11+ required')
    raw = os.environ.get('AUDIOFORGE_DATA_DIR', '')
    if not raw or not Path(raw).is_absolute():
        parser.error('set AUDIOFORGE_DATA_DIR to your explicit absolute data directory')
    data = Path(raw).resolve()
    if data == ROOT or ROOT in data.parents:
        parser.error('data must be outside the Workers source/release tree')
    if args.interval < 1:
        parser.error('interval must be >= 1 second')
    if args.role == 'generation' and not os.environ.get('AUDIOFORGE_BACKEND'):
        parser.error('set the approved AUDIOFORGE_BACKEND explicitly; no implicit mock service')
    sys.path.insert(0, str(PACKAGE_ROOT))
    os.environ['PYTHONPATH'] = str(PACKAGE_ROOT)  # Preserve this source for review subprocesses.
    package = importlib.import_module('audioforge')
    if Path(package.__file__).resolve() != PACKAGE_ROOT / 'audioforge/__init__.py':
        raise RuntimeError('AUDIOFORGE_SELECTED_SOURCE_REQUIRED')
    # Importing config is pure. Do not import api/service until after --check.
    from audioforge import config
    if args.check:
        print(json.dumps({'role': args.role, 'packageRoot': str(PACKAGE_ROOT), 'dataRoot': str(config.DATA_DIR),
                          'backend': config.BACKEND if args.role == 'generation' else None,
                          'startsWorker': False, 'opensDatabase': False, 'loadsModel': False}))
        return
    print('AudioForge selected Git source: ' + str(PACKAGE_ROOT), flush=True)
    if args.role == 'file-worker':
        sys.argv = [sys.argv[0], 'worker', '--interval', str(args.interval)]
    else:
        sys.argv = [sys.argv[0]]
    importlib.import_module('audioforge.' + ROLES[args.role]).main()


if __name__ == '__main__':
    main()
