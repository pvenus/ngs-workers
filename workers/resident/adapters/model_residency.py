"""Request-service model-memory authority; never instantiated inside a worker.

An expired reservation still consumes memory. Only trusted unload/process-exit
proof releases it. SQLite and transaction ownership belong to the Request service.
No production schema, process or API is started by importing this module.
"""
from contextlib import contextmanager
from dataclasses import asdict, dataclass
import json
import secrets
from ..contracts import PROFILES


@dataclass(frozen=True)
class ModelLease:
    grant_id: str
    worker_id: str
    role: str
    host_id: str
    model_id: str
    memory_bytes: int
    epoch: str
    token: str


def canonical(lease):
    return json.dumps(asdict(lease), sort_keys=True, separators=(',', ':'))


class ModelResidencyAuthority:
    def __init__(self, authority, transaction_lock, host_budgets, model_profiles,
                 *, verify_unloaded, lease_seconds=90):
        self.common, self.lock = authority, transaction_lock
        self.host_budgets = dict(host_budgets)
        self.models = {k: dict(v) for k, v in model_profiles.items()}
        self.verify_unloaded = verify_unloaded
        if not callable(verify_unloaded) or not 5 <= lease_seconds <= 3600:
            raise ValueError('MODEL_RESIDENCY_CONFIG_INVALID')
        if not self.host_budgets or any(type(n) is not int or n <= 0 for n in self.host_budgets.values()):
            raise ValueError('MODEL_MEMORY_BUDGET_INVALID')
        for role, model in self.models.items():
            if (role not in PROFILES
                    or set(model) != {'host_id', 'model_id', 'memory_bytes'}
                    or model['host_id'] not in self.host_budgets
                    or not isinstance(model['model_id'], str) or not model['model_id']
                    or type(model['memory_bytes']) is not int or model['memory_bytes'] <= 0
                    or model['memory_bytes'] > self.host_budgets[model['host_id']]):
                raise ValueError('MODEL_PROFILE_INVALID')
        self.lease_seconds = lease_seconds

    @contextmanager
    def transaction(self):
        with self.lock, self.common.tx():
            yield

    def initialize_schema(self):
        with self.transaction():
            self.common.db.execute('''CREATE TABLE IF NOT EXISTS resident_model_leases(
                grant_id TEXT PRIMARY KEY, worker_id TEXT NOT NULL, role TEXT NOT NULL,
                host_id TEXT NOT NULL, memory_bytes INTEGER NOT NULL,
                lease_json TEXT NOT NULL, expires_at REAL NOT NULL, released INTEGER NOT NULL DEFAULT 0)''')
            self.common.db.execute('''CREATE TABLE IF NOT EXISTS resident_model_commands(
                worker_id TEXT NOT NULL, command_key TEXT NOT NULL, role TEXT NOT NULL,
                grant_id TEXT, PRIMARY KEY(worker_id,command_key))''')

    def _row(self, lease, allow_released=False):
        row = self.common.db.execute('SELECT * FROM resident_model_leases WHERE grant_id=?',
                                     (lease.grant_id,)).fetchone()
        if row is None or row['lease_json'] != canonical(lease):
            raise ValueError('MODEL_LEASE_MISMATCH')
        if row['released'] and not allow_released:
            raise ValueError('MODEL_LEASE_RELEASED')
        return row

    def acquire(self, profile, worker_id, command_key):
        if PROFILES.get(profile.role) != profile or profile.role not in self.models:
            raise ValueError('MODEL_PROFILE_INVALID')
        if not isinstance(worker_id, str) or not worker_id or not isinstance(command_key, str) or not command_key:
            raise ValueError('MODEL_OWNER_REQUIRED')
        with self.transaction():
            old = self.common.db.execute('SELECT * FROM resident_model_commands WHERE worker_id=? AND command_key=?',
                                         (worker_id, command_key)).fetchone()
            if old:
                if old['role'] != profile.role:
                    raise ValueError('MODEL_COMMAND_CONFLICT')
                if old['grant_id'] is None:
                    return None
                row = self.common.db.execute('SELECT * FROM resident_model_leases WHERE grant_id=?',
                                             (old['grant_id'],)).fetchone()
                if row is None:
                    raise ValueError('MODEL_LEDGER_INCONSISTENT')
                return None if row['released'] else ModelLease(**json.loads(row['lease_json']))
            # A restarted owner must recover its previous durable command, not
            # reserve again under a fresh key and load another model instance.
            existing = self.common.db.execute('SELECT 1 FROM resident_model_leases WHERE worker_id=? AND role=? AND released=0',
                                              (worker_id, profile.role)).fetchone()
            if existing:
                raise ValueError('MODEL_RESIDENCY_RECOVERY_REQUIRED')
            config = self.models[profile.role]
            used = self.common.db.execute('SELECT COALESCE(SUM(memory_bytes),0) FROM resident_model_leases WHERE host_id=? AND released=0',
                                          (config['host_id'],)).fetchone()[0]
            lease = None
            if used + config['memory_bytes'] <= self.host_budgets[config['host_id']]:
                lease = ModelLease(secrets.token_hex(16), worker_id, profile.role,
                                   config['host_id'], config['model_id'], config['memory_bytes'],
                                   secrets.token_hex(16), secrets.token_hex(32))
                self.common.db.execute('INSERT INTO resident_model_leases VALUES(?,?,?,?,?,?,?,0)',
                    (lease.grant_id, worker_id, profile.role, lease.host_id, lease.memory_bytes,
                     canonical(lease), self.common.clock() + self.lease_seconds))
            self.common.db.execute('INSERT INTO resident_model_commands VALUES(?,?,?,?)',
                (worker_id, command_key, profile.role, lease.grant_id if lease else None))
            return lease

    def renew(self, lease):
        with self.transaction():
            try:
                row = self._row(lease)
            except ValueError:
                return False
            now = self.common.clock()
            if row['expires_at'] <= now:
                return False
            self.common.db.execute('UPDATE resident_model_leases SET expires_at=? WHERE grant_id=?',
                                   (now + self.lease_seconds, lease.grant_id))
            return True

    def release(self, lease):
        with self.transaction():
            row = self._row(lease, allow_released=True)
            if row['released']:
                return
            # This callback belongs to the service supervisor; never accept a
            # worker-supplied boolean or infer success merely from a timeout.
            if self.verify_unloaded(lease) is not True:
                raise ValueError('MODEL_UNLOAD_NOT_VERIFIED')
            self.common.db.execute('UPDATE resident_model_leases SET released=1 WHERE grant_id=?',
                                   (lease.grant_id,))

    def usage(self, host_id):
        with self.transaction():
            rows = self.common.db.execute('SELECT memory_bytes,expires_at FROM resident_model_leases WHERE host_id=? AND released=0',
                                          (host_id,)).fetchall()
            return {'reservedBytes': sum(r['memory_bytes'] for r in rows),
                    'reservations': len(rows),
                    'expiredRetained': sum(r['expires_at'] <= self.common.clock() for r in rows),
                    'budgetBytes': self.host_budgets[host_id]}
