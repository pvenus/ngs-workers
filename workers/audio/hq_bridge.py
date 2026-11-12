"""Hash-bound HQ package bridge. No model imports, queues or activation writes."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import threading

_LOAD_LOCK = threading.RLock()
_PACKAGE_FILES = ('__init__.py', 'backends.py', 'hq_contract.py', 'hq_backend.py', 'hq_pins.json')


def _canonical(value):
    path = Path(value)
    if not path.is_absolute() or path.resolve(strict=True) != path:
        raise ValueError('HQ_BRIDGE_NONCANONICAL_PATH')
    return path


def _read(path, limit=1024 * 1024):
    path = _canonical(path)
    for parent in path.parents:
        st = parent.stat()
        if st.st_uid not in (0, os.getuid()) or st.st_mode & 0o022:
            raise ValueError('HQ_BRIDGE_UNTRUSTED_PARENT')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_uid not in (0, os.getuid()) or before.st_mode & 0o022
                or before.st_size > limit):
            raise ValueError('HQ_BRIDGE_FILE_IDENTITY')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            data = stream.read(limit + 1)
        after, current = os.fstat(fd), path.lstat()
        def stamp(st):
            return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
        if len(data) != before.st_size or stamp(before) != stamp(after) or stamp(after) != stamp(current):
            raise ValueError('HQ_BRIDGE_CHANGED_READ')
        return data
    finally:
        os.close(fd)


def _package(config, paths):
    if config.get('schema') != 'private.music-hq-boundary.v1':
        raise ValueError('HQ_BRIDGE_SCHEMA')
    release = _canonical(config['release_root'])
    if release != Path(__file__).resolve().parents[2]:
        raise ValueError('HQ_BRIDGE_RELEASE_SCOPE')
    for key in ('runtime_root', 'artifact_root', 'runner_path'):
        if _canonical(config[key]) != _canonical(paths[key]):
            raise ValueError('HQ_BRIDGE_PATH_BINDING')
    runtime = _canonical(config['runtime_root'])
    launcher = Path(config['python_path'])
    if launcher != runtime / 'source/.venv/bin/python' or Path(paths['python_path']) != launcher:
        raise ValueError('HQ_BRIDGE_INTERPRETER_SCOPE')
    if launcher.parent.resolve(strict=True) != launcher.parent:
        raise ValueError('HQ_BRIDGE_INTERPRETER_SCOPE')
    python = launcher.resolve(strict=True)
    package = release / 'modules/audioforge/audioforge'
    runner = package / 'ace_hq_runner.py'
    if Path(config['runner_path']) != runner:
        raise ValueError('HQ_BRIDGE_RUNNER_SCOPE')
    expected = [release / 'workers/__init__.py', release / 'workers/audio/__init__.py',
                *[release / 'workers/audio' / n for n in ('boundary.py', 'legacy_backend.py', 'hq_bridge.py')],
                *[package / n for n in _PACKAGE_FILES], runner, python, runtime / 'source/.venv/pyvenv.cfg']
    pins = config['file_pins']
    if len(expected) != 13 or set(pins) != {str(p) for p in expected}:
        raise ValueError('HQ_BRIDGE_PIN_SET')
    verified = {}
    for path in expected:
        data = _read(path, 64 * 1024 * 1024 if path == python else 1024 * 1024)
        if hashlib.sha256(data).hexdigest() != pins[str(path)]:
            raise ValueError('HQ_BRIDGE_CODE_PIN')
        verified[str(path)] = data
    # hq_pins.json remains at its pinned package path for the backend's own checks.
    namespace = '_workers_hq_' + hashlib.sha256(
        (str(release) + json.dumps(pins, sort_keys=True)).encode()).hexdigest()
    created = []
    with _LOAD_LOCK:
        try:
            for name, filename, is_package in (
                (namespace, '__init__.py', True),
                (namespace + '.backends', 'backends.py', False),
                (namespace + '.hq_contract', 'hq_contract.py', False),
                (namespace + '.hq_backend', 'hq_backend.py', False),
            ):
                path = package / filename
                digest = pins[str(path)]
                if name in sys.modules:
                    module = sys.modules[name]
                    if getattr(module, '__file__', None) != str(path) or getattr(module, '_bridge_verified_sha256', None) != digest:
                        raise ValueError('HQ_BRIDGE_MODULE_COLLISION')
                    continue
                spec = importlib.util.spec_from_file_location(name, path,
                    submodule_search_locations=[str(package)] if is_package else None)
                module = importlib.util.module_from_spec(spec)
                sys.modules[name] = module
                created.append(name)
                exec(compile(verified[str(path)], str(path), 'exec'), module.__dict__)
                module._bridge_verified_sha256 = digest
            return sys.modules[namespace + '.hq_backend'].HQBackend
        except BaseException:
            for name in reversed(created):
                sys.modules.pop(name, None)
            raise


def available(config, paths):
    """Static runtime readiness only; never loads a model or starts inference."""
    try:
        backend = _package(config, paths)
        return backend.verify_runtime(paths['runtime_root'], deep=False) is not False
    except Exception:
        return False


def make_backend(config, paths, artifact, lock_fds, lock_validator, timeout):
    backend = _package(config, paths)
    root = _canonical(config['artifact_root'])
    artifact = _canonical(artifact)
    if artifact.parent != root or not artifact.name.startswith('content.') or not artifact.is_dir():
        raise ValueError('HQ_BRIDGE_ARTIFACT_SCOPE')
    if len(lock_fds) != 2 or any(type(fd) is not int or fd < 0 for fd in lock_fds) or not callable(lock_validator):
        raise ValueError('HQ_BRIDGE_LOCK_REQUIRED')
    lock_validator()
    return backend(runtime_root=paths['runtime_root'], artifact_root=artifact,
        runner_path=paths['runner_path'], python_path=paths['python_path'], enabled=True,
        lock_fds=tuple(lock_fds), lock_validator=lock_validator, timeout=timeout)
