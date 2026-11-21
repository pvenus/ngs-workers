"""Fail-closed OS exit proof for Request-service-owned launch records.

This module NEVER reads a worker JSON assertion, kills a process, creates a
launchd job, or treats a port/TTL as evidence. It is a candidate, not an active
binding. Existing backends do not yet populate the required trusted launch ledger.
A live retained Comfy session is deliberately NOT certified by process absence;
queue-idle + admission fencing needs a separate trusted integration.
"""
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import time
from typing import Callable
from ..terminal_evidence import ProcessIdentity, SourcePin, scope_key


@dataclass(frozen=True)
class ProcessRow:
    pid: int
    ppid: int
    pgid: int
    birth: str


@dataclass(frozen=True)
class Snapshot:
    taken_at: float
    boot_id: str
    rows: tuple[ProcessRow, ...]


@dataclass(frozen=True)
class LaunchBinding:
    # Created/sealed by a trusted service supervisor before/after dispatch, never
    # accepted through worker HTTP. revision identifies a frozen ledger snapshot.
    scope: str
    revision: str
    boot_id: str
    kind: str  # local-process-group or owned-comfy-session; remote is unsupported.
    processes: tuple[ProcessIdentity, ...]
    pins: tuple[SourcePin, ...]
    launch_admission_closed: bool
    complete_process_tree: bool
    no_detach_contract: bool


class MacProcessProbe:
    """Read-only bounded OS snapshot. Permission/error/parse failure => unknown."""
    def __init__(self, *, runner=subprocess.run, clock=time.monotonic):
        self.runner, self.clock = runner, clock

    def __call__(self):
        if platform.system() != 'Darwin':
            return None
        try:
            boot = self.runner(['/usr/sbin/sysctl', '-n', 'kern.boottime'],
                               capture_output=True, text=True, timeout=3, check=True).stdout.strip()
            if not boot.startswith('{ sec = '):
                return None
            data = self.runner(['/bin/ps', '-axo', 'pid=,ppid=,pgid=,lstart='],
                               capture_output=True, text=True, timeout=3, check=True).stdout
            rows = []
            for line in data.splitlines():
                parts = line.split()
                if len(parts) != 8:
                    return None
                pid, ppid, pgid = map(int, parts[:3])
                if min(pid, ppid, pgid) < 0:
                    return None
                rows.append(ProcessRow(pid, ppid, pgid, ' '.join(parts[3:])))
            if not rows or len({r.pid for r in rows}) != len(rows):
                return None
            return Snapshot(self.clock(), boot, tuple(rows))
        except (OSError, ValueError, subprocess.SubprocessError):
            return None


class ProcessProof:
    """Service-side callbacks for verify_stopped/verify_unloaded.

lookup(scope) must read a service-owned immutable launch ledger. still_sealed
must atomically confirm that its revision remains current and future dispatch is
fenced. Caller must NOT implement either callback from worker-owned files.
checkpoint(scope) returns a durable, independently PDS-verified checkpoint bound
to that exact scope hash, or None. It is corroboration, not a process exit proof.
"""
    def __init__(self, lookup: Callable, still_sealed: Callable, *, checkpoint=None,
                 probe=None, clock=time.monotonic, max_age=2):
        self.lookup, self.still_sealed, self.checkpoint = lookup, still_sealed, checkpoint
        self.probe = probe or MacProcessProbe(clock=clock)
        self.clock, self.max_age = clock, max_age
        if not callable(lookup) or not callable(still_sealed) or not 0 < max_age <= 5:
            raise ValueError('PROCESS_PROOF_CONFIG_INVALID')
        self.last_code = 'PROCESS_PROOF_NOT_RUN'

    def _deny(self, code):
        self.last_code = code
        return False

    def _prove(self, value, require_checkpoint):
        try:
            key = scope_key(value)
            binding = self.lookup(key)
            if not isinstance(binding, LaunchBinding) or binding.scope != key:
                return self._deny('TRUSTED_LAUNCH_BINDING_MISSING')
            if (binding.kind not in ('local-process-group', 'owned-comfy-session')
                    or not binding.revision or not binding.boot_id or not binding.processes
                    or not binding.pins or binding.launch_admission_closed is not True
                    or binding.complete_process_tree is not True or binding.no_detach_contract is not True):
                return self._deny('PROCESS_CLOSURE_UNPROVEN')
            if any(p.pid <= 1 or p.pgid <= 1 or not p.birth for p in binding.processes):
                return self._deny('PROCESS_IDENTITY_INVALID')
            for pin in binding.pins:
                path = Path(pin.path)
                if (not path.is_absolute() or path.is_symlink() or not path.is_file()
                        or hashlib.sha256(path.read_bytes()).hexdigest() != pin.sha256):
                    return self._deny('PROCESS_SOURCE_PIN_CHANGED')
            snapshot = self.probe()
            if (not isinstance(snapshot, Snapshot) or snapshot.boot_id != binding.boot_id
                    or not 0 <= self.clock() - snapshot.taken_at <= self.max_age):
                return self._deny('OS_PROCESS_SNAPSHOT_UNAVAILABLE')
            pids = {p.pid for p in binding.processes}
            groups = {p.pgid for p in binding.processes}
            # Any reused PID/group also denies: never infer absence from a changed
            # command or second-resolution ps birth time. This favors false negatives.
            if any(row.pid in pids or row.ppid in pids or row.pgid in groups for row in snapshot.rows):
                return self._deny('OWNED_PROCESS_OR_DESCENDANT_PRESENT')
            if require_checkpoint:
                proof = self.checkpoint(key) if self.checkpoint is not None else None
                if (not isinstance(proof, dict) or proof.get('scope') != key
                        or proof.get('authority') != 'filex' or proof.get('verified') is not True
                        or not isinstance(proof.get('receiptHash'), str) or len(proof['receiptHash']) != 64):
                    return self._deny('PDS_CHECKPOINT_UNVERIFIED')
            if self.still_sealed(binding) is not True:
                return self._deny('LAUNCH_BINDING_CHANGED')
            self.last_code = 'TRUSTED_PROCESS_EXIT_VERIFIED'
            return True
        except Exception:
            return self._deny('PROCESS_PROOF_UNKNOWN')

    def execution_stopped(self, claim):
        # Remote providers cannot be proved stopped by local child exit. They do
        # not receive a supported binding.kind and remain unknown.
        return self._prove(claim, require_checkpoint=True)

    def model_unloaded(self, model_lease):
        # Entire owned model process/group must be gone, not merely queue idle.
        return self._prove(model_lease, require_checkpoint=False)
