"""Minimal single-owner trusted-worker termination evidence, service-side only.

Unlike deployment.process_proof's stronger supervisor registry, this adapter
trusts the explicitly pinned worker/backend's terminal never_started/joined
statement. It does not trust user data or a worker boolean. A joined statement is
cross-checked against OS PID/group/descendant absence. Unknown OS evidence and
live retained Comfy sessions remain false. No process is started or killed here.

record_* must be exposed ONLY behind the authenticated worker identity routes.
Recording is terminal: pinned backend code must never spawn again for that exact
attempt/model lease. New work requires a new attempt/lease. No gate/shim is added.
"""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import time
from ..deployment.process_proof import Snapshot, MacProcessProbe
from ..terminal_evidence import (ProcessIdentity, SourcePin, scope_key, TerminalEvidence,
    profile_hash, evidence_from_hook, TrustedEvidenceReporter)


class TrustedProcessEvidence:
    def __init__(self, bridge, profiles, *, model_authority=None, probe=None,
                 clock=time.monotonic, max_age=2):
        self.bridge, self.common = bridge, bridge.common
        self.model_authority = model_authority
        self.profiles = {key: (role, tuple(pins)) for key, (role, pins) in profiles.items()}
        if not self.profiles or not 0 < max_age <= 5:
            raise ValueError('TERMINAL_EVIDENCE_CONFIG_INVALID')
        self.probe = probe or MacProcessProbe(clock=clock)
        self.clock, self.max_age = clock, max_age
        self.last_code = 'TERMINAL_EVIDENCE_NOT_CHECKED'

    def initialize_schema(self):
        with self.bridge.transaction():
            self.common.db.execute('''CREATE TABLE IF NOT EXISTS resident_terminal_evidence(
                scope_hash TEXT NOT NULL,scope_kind TEXT NOT NULL,worker_id TEXT NOT NULL,
                evidence_json TEXT NOT NULL,PRIMARY KEY(scope_hash,scope_kind))''')

    def _profile(self, evidence, role):
        configured = self.profiles.get(evidence.backend_id)
        if configured is None or configured[0] != role:
            raise ValueError('TERMINAL_BACKEND_NOT_APPROVED')
        pins = configured[1]
        if not pins or evidence.profile_hash != profile_hash(role, pins):
            raise ValueError('TERMINAL_PROFILE_MISMATCH')
        for pin in pins:
            path = Path(pin.path)
            if (not path.is_absolute() or path.is_symlink() or not path.is_file()
                    or hashlib.sha256(path.read_bytes()).hexdigest() != pin.sha256):
                raise ValueError('TERMINAL_SOURCE_PIN_CHANGED')

    def _validate(self, scope, evidence, role):
        if not isinstance(evidence, TerminalEvidence) or evidence.scope != scope_key(scope):
            raise ValueError('TERMINAL_SCOPE_MISMATCH')
        self._profile(evidence, role)
        if evidence.outcome not in ('never_started', 'joined'):
            raise ValueError('TERMINAL_OUTCOME_INVALID')
        if evidence.outcome == 'never_started' and evidence.processes:
            raise ValueError('TERMINAL_PROCESS_LIST_INVALID')
        if (len(evidence.return_codes) != len(evidence.processes)
                or any(type(code) is not int for code in evidence.return_codes)):
            raise ValueError('TERMINAL_WAIT_EVIDENCE_REQUIRED')
        for process in evidence.processes:
            if (not isinstance(process, ProcessIdentity) or type(process.pid) is not int
                    or type(process.pgid) is not int or process.pid <= 1 or process.pgid <= 1
                    or not isinstance(process.birth, str) or not process.birth):
                raise ValueError('TERMINAL_PROCESS_IDENTITY_INVALID')

    def _save(self, value, worker_id, evidence, role, kind):
        self._validate(value, evidence, role)
        raw = json.dumps(asdict(evidence), sort_keys=True, separators=(',', ':'))
        key = scope_key(value)
        self.common.db.execute('INSERT OR IGNORE INTO resident_terminal_evidence VALUES(?,?,?,?)',
                               (key, kind, worker_id, raw))
        saved = self.common.db.execute('SELECT worker_id,evidence_json FROM resident_terminal_evidence WHERE scope_hash=? AND scope_kind=?',
                                      (key, kind)).fetchone()
        if saved['worker_id'] != worker_id or saved['evidence_json'] != raw:
            raise ValueError('TERMINAL_EVIDENCE_CHANGED')

    def record_execution(self, claim, worker_id, evidence):
        with self.bridge.transaction():
            lease = self.bridge._issued_lease(claim, allow_released=True)
            if lease['workerId'] != worker_id:
                raise ValueError('TERMINAL_WORKER_MISMATCH')
            # Canonical capability role, never a caller-selected resource name.
            from ..contracts import PROFILES
            roles = [p.role for p in PROFILES.values() if claim.capability in p.capabilities]
            if len(roles) != 1:
                raise ValueError('TERMINAL_CAPABILITY_UNKNOWN')
            self._save(claim, worker_id, evidence, roles[0], 'execution')

    def record_model(self, lease, worker_id, evidence):
        if self.model_authority is None or lease.worker_id != worker_id:
            raise ValueError('TERMINAL_MODEL_OWNER_MISMATCH')
        # Model and Request authorities must share the service ledger/lock. This
        # candidate intentionally refuses a cross-database atomicity assumption.
        if self.model_authority.common.db is not self.common.db:
            raise ValueError('TERMINAL_MODEL_LEDGER_MISMATCH')
        with self.bridge.transaction():
            self.model_authority._row(lease, allow_released=True)
            self._save(lease, worker_id, evidence, lease.role, 'model')

    def _verify(self, value, kind):
        try:
            key = scope_key(value)
            with self.bridge.transaction():
                row = self.common.db.execute('SELECT evidence_json FROM resident_terminal_evidence WHERE scope_hash=? AND scope_kind=?',
                                             (key, kind)).fetchone()
            if row is None:
                raise ValueError('TERMINAL_EVIDENCE_MISSING')
            raw = json.loads(row[0]); raw['processes'] = tuple(ProcessIdentity(**p) for p in raw['processes'])
            raw['return_codes'] = tuple(raw['return_codes'])
            evidence = TerminalEvidence(**raw)
            role = value.role if kind == 'model' else next(
                role for role, pins in self.profiles.values()
                if profile_hash(role, pins) == evidence.profile_hash)
            self._validate(value, evidence, role)
            snapshot = self.probe()
            if (not isinstance(snapshot, Snapshot) or not snapshot.boot_id or not snapshot.rows
                    or not 0 <= self.clock() - snapshot.taken_at <= self.max_age):
                raise ValueError('TERMINAL_OS_SNAPSHOT_UNKNOWN')
            pids = {p.pid for p in evidence.processes}; groups = {p.pgid for p in evidence.processes}
            # Any PID/group reuse is a conservative denial. Birth differences do
            # not turn an existing process into proof of absence.
            if any(r.pid in pids or r.ppid in pids or r.pgid in groups for r in snapshot.rows):
                raise ValueError('TERMINAL_PROCESS_GROUP_PRESENT')
            self.last_code = 'TRUSTED_TERMINAL_EVIDENCE_VERIFIED'
            return True
        except Exception as exc:
            code = str(exc)
            self.last_code = code if code.startswith('TERMINAL_') else 'TERMINAL_EVIDENCE_UNKNOWN'
            return False

    def verify_stopped(self, claim):
        return self._verify(claim, 'execution')

    def verify_unloaded(self, lease):
        return self._verify(lease, 'model')

