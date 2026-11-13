"""Fail-closed, explicit music boundary. No queues, retries, or model imports."""
import fcntl
import hashlib
import json
import os
import stat
from pathlib import Path


def canonical_path(value):
    p = Path(value)
    if not p.is_absolute() or str(p.resolve(strict=True)) != str(p):
        raise ValueError('BOUNDARY_NONCANONICAL_PATH')
    return p


def check_identity(st, pin):
    if (st.st_dev, st.st_ino) != (pin['device'], pin['inode']):
        raise ValueError('BOUNDARY_STALE_IDENTITY')


def open_existing_lock(pin):
    p = canonical_path(pin['path'])
    fd = os.open(p, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        st = os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_nlink != 1
                or st.st_uid != os.getuid() or st.st_mode & 0o022):
            raise ValueError('BOUNDARY_INVALID_LOCK')
        check_identity(st, pin)
        check_identity(p.stat(), pin)
        return fd
    except BaseException:
        os.close(fd)
        raise


def validate_lock_fd(fd, pin):
    if type(fd) is not int or fd < 0:
        raise ValueError('BOUNDARY_REQUIRED_LOCK_FD')
    check_identity(os.fstat(fd), pin)
    probe = open_existing_lock(pin)
    try:
        try:
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        raise ValueError('BOUNDARY_LOCK_NOT_HELD')
    finally:
        os.close(probe)


def validate_config(config, artifacts):
    hq = config.get('schema') == 'private.music-hq-boundary.v1'
    if not hq and config.get('schema') != 'private.music-boundary.v1':
        raise ValueError('BOUNDARY_CONFIG_SCHEMA')
    paths = {k: canonical_path(config[k]) for k in ('runtime_root', 'artifact_root', 'runner_path')}
    launcher = Path(config['python_path'])
    expected = paths['runtime_root'] / 'source/.venv/bin/python'
    if launcher != expected or launcher.parent.resolve(strict=True) != launcher.parent:
        raise ValueError('BOUNDARY_INTERPRETER_LAUNCHER')
    target = launcher.resolve(strict=True)
    venv_config = canonical_path(str(paths['runtime_root'] / 'source/.venv/pyvenv.cfg'))
    paths['python_path'] = launcher
    artifact = canonical_path(str(artifacts))
    if (artifact.parent != paths['artifact_root'] or not artifact.name.startswith('content.')) and artifact != paths['artifact_root']:
        raise ValueError('BOUNDARY_ARTIFACT_SCOPE')
    if not artifact.is_dir():
        raise ValueError('BOUNDARY_ARTIFACT_SCOPE')
    for key in ('runtime_root', 'artifact_root'):
        check_identity(paths[key].stat(), config['directory_pins'][key])
    required = {str(paths['runner_path']), str(target), str(venv_config)}
    required.add(str(Path(__file__).parents[1].joinpath('__init__.py').resolve()))
    required.update(str(Path(__file__).with_name(n).resolve()) for n in ('boundary.py','legacy_backend.py','__init__.py'))
    if hq:
        release = canonical_path(config['release_root'])
        if release != Path(__file__).resolve().parents[2]:
            raise ValueError('BOUNDARY_HQ_RELEASE_SCOPE')
        package = release / 'modules/audioforge/audioforge'
        if paths['runner_path'] != package / 'ace_hq_runner.py':
            raise ValueError('BOUNDARY_HQ_RUNNER_SCOPE')
        required.add(str(Path(__file__).with_name('hq_bridge.py').resolve()))
        required.update(str(package / n) for n in ('__init__.py', 'backends.py',
            'hq_contract.py', 'hq_backend.py', 'hq_pins.json'))
    pins = config['file_pins']
    if required != set(pins):
        raise ValueError('BOUNDARY_MISSING_CODE_PIN')
    for name, digest in pins.items():
        p = canonical_path(name)
        if hashlib.sha256(p.read_bytes()).hexdigest() != digest:
            raise ValueError('BOUNDARY_STALE_CODE')
    # Trusted owner binding assigns two independent coordination roles. The
    # legacy lock stays at its pinned legacy location when model runtime moves.
    shared, legacy = config['shared_lock'], config['legacy_lock']
    if (shared['path'] == legacy['path'] or
            (shared['device'], shared['inode']) == (legacy['device'], legacy['inode'])):
        raise ValueError('BOUNDARY_LOCK_ROLE_COLLISION')
    for name in ('shared_lock', 'legacy_lock'):
        fd = open_existing_lock(config[name])
        os.close(fd)
    return paths, artifact


def hq_available(config):
    """Metadata-only installation check, not a model load or generation test."""
    if config.get('schema') != 'private.music-hq-boundary.v1':
        return False
    try:
        paths, _ = validate_config(config, config['artifact_root'])
        from .hq_bridge import available
        return available(config, paths)
    except Exception:
        return False


def generate_music(config, artifacts, prompt, duration, seed, model, output, cancelled, *, timeout=1100, settings=None):
    paths, artifact = validate_config(config, artifacts)
    hq = config.get('schema') == 'private.music-hq-boundary.v1'
    if (model == 'acestep-xl-sft-lm4b') != hq:
        raise ValueError('BOUNDARY_PROFILE_BINDING_MISMATCH')
    if artifact == paths['artifact_root']:
        raise ValueError('BOUNDARY_REQUEST_DIRECTORY_REQUIRED')
    fd = open_existing_lock(config['shared_lock'])
    legacy_fd = None
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('BOUNDARY_SHARED_BUSY_NO_RETRY') from exc
        legacy_fd = open_existing_lock(config['legacy_lock'])
        try:
            fcntl.flock(legacy_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('BOUNDARY_LEGACY_BUSY_NO_RETRY') from exc
        # Recheck after acquiring: stale configuration denies before backend/spawn.
        validate_config(config, artifacts)
        if hq:
            from .hq_bridge import make_backend
            def recheck():
                validate_config(config, artifacts)
                validate_lock_fd(fd, config['shared_lock'])
                validate_lock_fd(legacy_fd, config['legacy_lock'])
            backend = make_backend(config, paths, artifact, (fd, legacy_fd), recheck, timeout)
            try:
                return backend.generate(prompt, duration, seed, model, output, cancelled, settings)
            except Exception as exc:
                # The separately pinned AudioForge package has its own exception
                # class. Translate only that verified class into this boundary's.
                package = __import__(backend.__class__.__module__, fromlist=['Cancelled'])
                if isinstance(exc, package.Cancelled):
                    from . import Cancelled
                    raise Cancelled() from exc
                raise
        else:
            from .legacy_backend import AceStepBackend
            backend = AceStepBackend(runtime_root=paths['runtime_root'], artifact_root=artifact,
                runner_path=paths['runner_path'], python_path=paths['python_path'],
                shared_lock_fd=fd, shared_lock_pin=config['shared_lock'],
                legacy_lock_fd=legacy_fd, legacy_lock_pin=config['legacy_lock'], timeout=timeout)
            return backend.generate(prompt, duration, seed, model, output, cancelled, settings)
    finally:
        # Never LOCK_UN: inherited child holds this open file description too.
        if legacy_fd is not None:
            os.close(legacy_fd)
        os.close(fd)

