"""Worker-safe terminal evidence DTO and trusted backend hook wrapper. No DB/OS imports."""
from dataclasses import asdict, dataclass
import hashlib
import json


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    pgid: int
    birth: str


@dataclass(frozen=True)
class SourcePin:
    path: str
    sha256: str


def scope_key(value):
    return hashlib.sha256(json.dumps(asdict(value), sort_keys=True,
        separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


@dataclass(frozen=True)
class TerminalEvidence:
    scope: str
    backend_id: str
    profile_hash: str
    outcome: str
    processes: tuple[ProcessIdentity, ...]
    return_codes: tuple[int, ...]


def profile_hash(role, pins):
    return hashlib.sha256(json.dumps({'role': role, 'pins': [asdict(p) for p in pins]},
        sort_keys=True, separators=(',', ':')).encode()).hexdigest()



def evidence_from_hook(value, backend_id, role, pins, hook):
    """Called by trusted operator-configured reporter, never on user input."""
    if (not isinstance(hook, dict)
            or set(hook) - {'kind'} != {'outcome', 'processGroups', 'returnCodes'}
            or hook.get('kind', 'local-processes') != 'local-processes'
            or hook['outcome'] not in ('never_started', 'joined')
            or not isinstance(hook['processGroups'], (list, tuple))
            or not isinstance(hook['returnCodes'], (list, tuple))
            or len(hook['processGroups']) != len(hook['returnCodes'])
            or any(type(g) is not int or g <= 1 for g in hook['processGroups'])
            or any(type(code) is not int for code in hook['returnCodes'])
            or (hook['outcome'] == 'never_started' and hook['processGroups'])):
        raise ValueError('TERMINAL_BACKEND_HOOK_UNCONFIRMED')
    # Existing owned subprocesses use start_new_session, so group leader PID=PGID.
    # Birth is not fabricated: any reused PID/group is denied by the OS cross-check.
    return TerminalEvidence(scope_key(value), backend_id, profile_hash(role, pins),
        hook['outcome'], tuple(ProcessIdentity(g, g, 'not-collected') for g in hook['processGroups']),
        tuple(hook['returnCodes']))


class TrustedEvidenceReporter:
    """Worker wrapper: injects approved deployment profile, forwards to HTTP port."""
    def __init__(self, port, backend_id, role, pins):
        self.port, self.backend_id, self.role, self.pins = port, backend_id, role, tuple(pins)
    def record_execution(self, claim, worker_id, hook):
        evidence = evidence_from_hook(claim, self.backend_id, self.role, self.pins, hook)
        return self.port.record_execution(claim, worker_id, evidence)
    def record_model(self, lease, worker_id, hook):
        evidence = evidence_from_hook(lease, self.backend_id, self.role, self.pins, hook)
        return self.port.record_model(lease, worker_id, evidence)
