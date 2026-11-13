"""ImageForge's single, fail-closed resource location and file publisher.

No directory under the configured resource root is created at import time.
The operational volume gate deliberately stays closed until an administrator
records the volume UUID and physical external bus in the environment.
"""
from __future__ import annotations

import hashlib
import io
import json
import mimetypes
import os
import plistlib
import re
import stat
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(os.environ.get("IMAGEFORGE_LEGACY_ROOT", Path(__file__).resolve().parents[1])).expanduser().resolve()
LEGACY_DOWNLOADS = PROJECT_ROOT / "imageforge-downloads"
LEGACY_GENERATED = PROJECT_ROOT / "generated_image"
INTERNAL_STORAGE = os.environ.get("IMAGEFORGE_STORAGE_MODE") == "internal"
MOUNT = Path(os.environ.get("IMAGEFORGE_STORAGE_BASE", str(Path.home() / "Workspaces/Workers/data/imageforge"))) if INTERNAL_STORAGE else Path("/Volumes/WorkersArchive")
DEFAULT_ROOT = MOUNT / ("resources/v1" if INTERNAL_STORAGE else "MCP/resources/v1")
SERVICE = "imageforge"
LEGACY_INVENTORY = json.loads(Path(os.environ.get("IMAGEFORGE_LEGACY_INVENTORY", Path(__file__).with_name("legacy_inventory.json"))).read_text(encoding="utf-8"))


class StorageUnavailable(RuntimeError):
    pass


class StorageConflict(RuntimeError):
    pass


def shared_root() -> Path:
    expected_root = MOUNT / ("resources/v1" if INTERNAL_STORAGE else "MCP/resources/v1")
    value = os.environ.get("IMAGEFORGE_RESOURCE_ROOT", str(expected_root))
    root = Path(value)
    if not root.is_absolute():
        raise StorageUnavailable("IMAGEFORGE_RESOURCE_ROOT must be absolute")
    if root != expected_root:
        raise StorageUnavailable("ImageForge root must match the provisioned namespace contract")
    return root


def service_root() -> Path:
    return shared_root() / SERVICE


def downloads_root() -> Path:
    return service_root() / "jobs"


def generated_root() -> Path:
    return service_root() / "jobs"


def new_job(job_id: str, kind: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", job_id) or kind not in {"original", "intermediate", "adopted"}:
        raise StorageUnavailable("Invalid ImageForge job or artifact class")
    ensure_directory(service_root() / "jobs")
    job = new_directory(service_root() / "jobs", job_id)
    return new_directory(job, kind)


def _disk_info(path: Path) -> dict[str, Any]:
    try:
        result = subprocess.run(
            ["/usr/sbin/diskutil", "info", "-plist", str(path)],
            check=True, capture_output=True, timeout=5,
        )
        return plistlib.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise StorageUnavailable("Cannot verify ImageForge volume identity") from exc


def _mount_state() -> tuple[os.stat_result, os.stat_result, os.statvfs_result]:
    return MOUNT.lstat(), MOUNT.parent.stat(), os.statvfs(MOUNT)


def _has_acl(path: Path) -> bool:
    """macOS ls marks an ACL with '+' in the mode field; query failures close the gate."""
    try:
        result = subprocess.run(["/bin/ls", "-lde", str(path)], check=True,
                                capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError) as exc:
        raise StorageUnavailable("Cannot verify ImageForge directory ACL") from exc
    lines = result.stdout.splitlines()
    fields = lines[0].split() if lines else []
    if not fields or len(fields[0]) < 10:
        raise StorageUnavailable("Cannot parse ImageForge directory ACL")
    return fields[0].endswith("+") or any(re.match(r"^\s*\d+:", line)
                                          for line in lines[1:])


def _directory_contract(path: Path, *, uid: int, gid: int, mode: int, device: int) -> None:
    try:
        item = path.lstat()
    except OSError as exc:
        raise StorageUnavailable("ImageForge directory requires administrator provisioning") from exc
    if (not stat.S_ISDIR(item.st_mode) or item.st_dev != device
            or item.st_uid != uid or item.st_gid != gid
            or stat.S_IMODE(item.st_mode) != mode or _has_acl(path)):
        raise StorageUnavailable("ImageForge directory ownership, mode or ACL mismatch")


def guard_volume() -> None:
    """Require a mounted, writable, owned external volume before any mkdir/write."""
    if INTERNAL_STORAGE:
        # Explicit provisioned internal root; preserve no-symlink and same-device boundaries.
        if not MOUNT.is_absolute() or MOUNT.resolve(strict=True) != MOUNT:
            raise StorageUnavailable("Internal storage root must be canonical")
        info = MOUNT.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise StorageUnavailable("Internal storage root ownership/mode mismatch")
        if os.statvfs(MOUNT).f_flag & getattr(os, "ST_RDONLY", 1):
            raise StorageUnavailable("Internal storage is read only")
        _check_existing(service_root(), device=info.st_dev)
        for path in (MOUNT / "resources", shared_root()):
            _directory_contract(path, uid=os.getuid(), gid=os.getgid(), mode=0o755, device=info.st_dev)
        _directory_contract(service_root(), uid=os.getuid(), gid=os.getgid(), mode=0o700, device=info.st_dev)
        return
    expected_uuid = os.environ.get("IMAGEFORGE_VOLUME_UUID", "").lower()
    expected_device = os.environ.get("IMAGEFORGE_DEVICE_IDENTIFIER", "")
    expected_store = os.environ.get("IMAGEFORGE_PHYSICAL_STORE", "")
    expected_bus = os.environ.get("IMAGEFORGE_EXTERNAL_BUS", "")
    if not all((expected_uuid, expected_device, expected_store, expected_bus)):
        raise StorageUnavailable("ImageForge volume identity is unverified")
    try:
        mount_stat, parent_stat, vfs = _mount_state()
    except OSError as exc:
        raise StorageUnavailable("ImageForge external volume is absent") from exc
    if not stat.S_ISDIR(mount_stat.st_mode) or mount_stat.st_dev == parent_stat.st_dev:
        raise StorageUnavailable("ImageForge external volume is not mounted")
    if vfs.f_flag & getattr(os, "ST_RDONLY", 1):
        raise StorageUnavailable("ImageForge external volume is read only")
    info = _disk_info(MOUNT)
    if (str(info.get("VolumeUUID", "")).lower() != expected_uuid
            or str(info.get("DeviceIdentifier", "")) != expected_device):
        raise StorageUnavailable("ImageForge external volume identity mismatch")
    stores = info.get("APFSPhysicalStores", [])
    store_ids = {str(item.get("DeviceIdentifier", "")) if isinstance(item, dict) else str(item)
                 for item in stores} if isinstance(stores, list) else set()
    if expected_store not in store_ids:
        raise StorageUnavailable("ImageForge physical store linkage is unverified")
    physical = _disk_info(Path("/dev") / expected_store)
    if (str(physical.get("DeviceIdentifier", "")) != expected_store
            or str(physical.get("BusProtocol", "")) != expected_bus
            or physical.get("Internal") is not False):
        raise StorageUnavailable("ImageForge physical external bus mismatch")
    try:
        mounted = subprocess.run(["/sbin/mount"], check=True, capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise StorageUnavailable("Cannot verify ImageForge volume ownership") from exc
    line = next((line for line in mounted.splitlines() if f" on {MOUNT} (" in line), "")
    if not line or "read-only" in line:
        raise StorageUnavailable("ImageForge volume mount or write availability is unverified")
    root = shared_root()
    namespace = service_root()
    _check_existing(namespace, device=mount_stat.st_dev)
    # Every ancestor must resist rename by the login UID, not only the leaf.
    # On this noowners volume these checks catch accidents, not another
    # process with the same UID. The service namespace remains a path boundary.
    for path in (MOUNT / "MCP", MOUNT / "MCP/resources", root):
        _directory_contract(path, uid=os.getuid(), gid=os.getgid(),
                            mode=0o755, device=mount_stat.st_dev)
    _directory_contract(namespace, uid=os.getuid(), gid=os.getgid(),
                        mode=0o700, device=mount_stat.st_dev)


def _parts(path: Path, root: Path) -> tuple[str, ...]:
    if not path.is_absolute() or not path.is_relative_to(root):
        raise StorageUnavailable("ImageForge path is outside its service namespace")
    parts = path.relative_to(root).parts
    if any(p in ("", ".", "..") or "/" in p or "\\" in p or any(ord(c) < 32 for c in p) for p in parts):
        raise StorageUnavailable("Invalid ImageForge path component")
    return parts


def _check_existing(path: Path, *, device: int) -> None:
    """Reject symlinks and cross-device components, including the shared root."""
    current = MOUNT
    for part in path.relative_to(MOUNT).parts:
        current /= part
        try:
            item = current.lstat()
        except FileNotFoundError:
            break
        if stat.S_ISLNK(item.st_mode) or item.st_dev != device:
            raise StorageUnavailable("ImageForge path escapes the verified volume")


def ensure_directory(path: Path) -> None:
    guard_volume()
    root = service_root()
    _parts(path, root)
    device = MOUNT.stat().st_dev
    _check_existing(path, device=device)
    fd = os.open(MOUNT, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0))
    try:
        for part in path.relative_to(MOUNT).parts:
            guard_volume()
            try:
                os.mkdir(part, mode=0o750, dir_fd=fd)
            except FileExistsError:
                pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0), dir_fd=fd)
            if os.fstat(child).st_dev != device:
                os.close(child)
                raise StorageUnavailable("ImageForge directory collision or device change")
            os.close(fd)
            fd = child
        os.fsync(fd)
    finally:
        os.close(fd)
    guard_volume()


def new_directory(parent: Path, name: str) -> Path:
    if not re.fullmatch(r"[\w.-]{1,100}", name) or name in (".", ".."):
        raise StorageUnavailable("Invalid ImageForge directory name")
    path = parent / name
    guard_volume()
    _parts(path, service_root())
    if path.exists() or path.is_symlink():
        raise StorageConflict(f"ImageForge output already exists: {path}")
    ensure_directory(parent)
    guard_volume()
    fd = _open_directory(parent)
    try:
        guard_volume()
        try:
            os.mkdir(name, mode=0o750, dir_fd=fd)
        except FileExistsError as exc:
            raise StorageConflict(f"ImageForge output already exists: {path}") from exc
        os.fsync(fd)
    finally:
        os.close(fd)
    return path


def _open_directory(path: Path) -> int:
    _parts(path, service_root())
    device = MOUNT.stat().st_dev
    fd = os.open(MOUNT, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0))
    try:
        for part in path.relative_to(MOUNT).parts:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0), dir_fd=fd)
            if os.fstat(child).st_dev != device:
                os.close(child)
                raise StorageUnavailable("ImageForge path changed device")
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def atomic_bytes(path: Path, data: bytes) -> str:
    """Publish once using exclusive create; never replace an existing file."""
    guard_volume()
    _parts(path, service_root())
    ensure_directory(path.parent)
    _check_existing(path, device=MOUNT.stat().st_dev)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    directory_fd = _open_directory(path.parent)
    try:
        fd = os.open(temporary.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o640, dir_fd=directory_fd)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        guard_volume()
        try:
            os.link(temporary.name, path.name, src_dir_fd=directory_fd,
                    dst_dir_fd=directory_fd, follow_symlinks=False)
        except FileExistsError as exc:
            raise StorageConflict(f"ImageForge output already exists: {path}") from exc
        os.fsync(directory_fd)
    finally:
        try:
            os.unlink(temporary.name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        os.close(directory_fd)
    return hashlib.sha256(data).hexdigest()


def save_png(image: Any, path: Path) -> str:
    output = io.BytesIO()
    image.save(output, "PNG")
    return atomic_bytes(path, output.getvalue())


def save_json(path: Path, value: Any) -> str:
    return atomic_bytes(path, json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8"))


def commit(directory: Path, *, origin: str, source: str | None = None,
           settings: dict[str, Any] | None = None) -> None:
    guard_volume()
    _parts(directory, service_root())
    _check_existing(directory, device=MOUNT.stat().st_dev)
    files = []
    for path in sorted(directory.iterdir()):
        if path.name.startswith(".imageforge-"):
            continue
        item = path.lstat()
        if not stat.S_ISREG(item.st_mode) or item.st_dev != MOUNT.stat().st_dev:
            raise StorageUnavailable("Unexpected object in ImageForge package")
        data = path.read_bytes()
        files.append({"path": path.name, "bytes": len(data),
                      "sha256": hashlib.sha256(data).hexdigest(),
                      "mime": mimetypes.guess_type(path.name)[0] or "application/octet-stream"})
    relative = directory.relative_to(service_root()).parts
    job_id = relative[1] if len(relative) > 1 and relative[0] == "jobs" else None
    parent_hash = None
    if source:
        source_path = Path(source)
        if source_path.is_absolute() and source_path.is_file() and not source_path.is_symlink():
            parent_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
    manifest = {"schema_version": 1, "service": SERVICE, "job_id": job_id,
                "logical_path": directory.relative_to(service_root()).as_posix(),
                "origin": origin, "source": source, "parent_sha256": parent_hash,
                "settings": settings or {},
                "adoption_reason": "explicit save request" if origin == "adopted" else None,
                "created_at": datetime.now(timezone.utc).isoformat(), "status": "committed",
                "files": files}
    digest = save_json(directory / ".imageforge-manifest.json", manifest)
    atomic_bytes(directory / ".imageforge-commit", (digest + "\n").encode("ascii"))


def verified_file(path: Path) -> bool:
    """Only expose committed new packages and containment-safe legacy files."""
    if not path.is_file() or path.is_symlink():
        return False
    if path.is_relative_to(LEGACY_DOWNLOADS) or path.is_relative_to(LEGACY_GENERATED):
        root = LEGACY_DOWNLOADS if path.is_relative_to(LEGACY_DOWNLOADS) else LEGACY_GENERATED
        relative = path.relative_to(PROJECT_ROOT).as_posix()
        entry = LEGACY_INVENTORY.get(relative)
        if entry is None or not path.resolve().is_relative_to(root.resolve()):
            return False
        current = root
        for part in path.relative_to(root).parts:
            current /= part
            if stat.S_ISLNK(current.lstat().st_mode):
                return False
        data = path.read_bytes()
        return len(data) == entry["bytes"] and hashlib.sha256(data).hexdigest() == entry["sha256"]
    if not path.is_relative_to(service_root()):
        return False
    try:
        guard_volume()
        _check_existing(path, device=MOUNT.stat().st_dev)
        directory = path.parent
        manifest_bytes = (directory / ".imageforge-manifest.json").read_bytes()
        marker = (directory / ".imageforge-commit").read_text().strip()
        if hashlib.sha256(manifest_bytes).hexdigest() != marker:
            return False
        entries = json.loads(manifest_bytes)["files"]
        entry = next(e for e in entries if e["path"] == path.name)
        data = path.read_bytes()
        return len(data) == entry["bytes"] and hashlib.sha256(data).hexdigest() == entry["sha256"]
    except (OSError, ValueError, KeyError, StopIteration, StorageUnavailable):
        return False


def read_verified_bytes(path: Path) -> bytes:
    """Read through no-follow descriptors and verify the frozen or committed hash."""
    if not verified_file(path):
        raise StorageUnavailable("ImageForge file is unverified")
    if path.is_relative_to(LEGACY_DOWNLOADS) or path.is_relative_to(LEGACY_GENERATED):
        root = LEGACY_DOWNLOADS if path.is_relative_to(LEGACY_DOWNLOADS) else LEGACY_GENERATED
        expected = LEGACY_INVENTORY[path.relative_to(PROJECT_ROOT).as_posix()]
    else:
        guard_volume()
        root = MOUNT
        manifest_bytes = (path.parent / ".imageforge-manifest.json").read_bytes()
        marker = (path.parent / ".imageforge-commit").read_text().strip()
        if hashlib.sha256(manifest_bytes).hexdigest() != marker:
            raise StorageUnavailable("ImageForge commit marker mismatch")
        expected = next(item for item in json.loads(manifest_bytes)["files"] if item["path"] == path.name)
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0))
    try:
        parts = path.relative_to(root).parts
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0), dir_fd=fd)
            os.close(fd)
            fd = child
        file_fd = os.open(parts[-1], os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=fd)
        with os.fdopen(file_fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise StorageUnavailable("ImageForge object is not a regular file")
            data = stream.read()
    finally:
        os.close(fd)
    if len(data) != expected["bytes"] or hashlib.sha256(data).hexdigest() != expected["sha256"]:
        raise StorageUnavailable("ImageForge file hash changed during read")
    return data
