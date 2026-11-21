"""Service-side retained Comfy proof. No worker history/boolean is trusted.

Authenticated pinned worker routes alone may register/seal. The service keeps
video-global admission while verifying; registration failure requires shutdown.
"""
import json
import math
import urllib.request
from ..contracts import PROFILES
from .model_residency import ModelLease, canonical


class LocalComfyProbe:
    def __call__(self, engine, prompts):
        import os, psutil
        p = psutil.Process(engine['pid'])
        if p.create_time() != engine['createTime'] or os.getpgid(p.pid) != engine['pgid']:
            return False
        if not any(c.status == psutil.CONN_LISTEN and c.laddr.ip == '127.0.0.1'
                   and c.laddr.port == 8192 for c in p.net_connections(kind='tcp')):
            return False
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                raise ValueError('COMFY_REDIRECT_DENIED')
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        def get(path):
            with opener.open('http://127.0.0.1:8192'+path, timeout=3) as response:
                return json.load(response)
        # All graph phases, not only the final decoder, must be terminal.
        for prompt in prompts:
            history = get('/history/'+prompt).get(prompt, {})
            status = history.get('status', {})
            if status.get('completed') is not True or status.get('status_str') != 'success':
                return False
        queue = get('/queue')
        if queue.get('queue_running') != [] or queue.get('queue_pending') != []:
            return False
        return p.is_running() and p.create_time() == engine['createTime'] and os.getpgid(p.pid) == engine['pgid']


class RetainedAnimationAuthority:
    def __init__(self, bridge, model_authority, *, trusted_worker, probe=None):
        if (model_authority.common is not bridge.common and model_authority.common.db is not bridge.common.db) or not callable(trusted_worker):
            raise ValueError('RETAINED_AUTHORITY_CONFIG_INVALID')
        self.bridge, self.models = bridge, model_authority
        self.common = bridge.common
        self.trusted_worker = trusted_worker
        self.probe = probe or LocalComfyProbe()

    def initialize_schema(self):
        with self.bridge.transaction():
            self.common.db.execute('''CREATE TABLE IF NOT EXISTS resident_animation_sessions(
                grant_id TEXT PRIMARY KEY,model_json TEXT NOT NULL,worker_id TEXT NOT NULL,
                engine_json TEXT NOT NULL,prompts_json TEXT NOT NULL,sealed INTEGER NOT NULL DEFAULT 0)''')
            self.common.db.execute('CREATE TABLE IF NOT EXISTS resident_animation_idle_receipts(grant_id TEXT PRIMARY KEY,session_json TEXT NOT NULL)')

    def _current(self, claim, model, worker, *, live=True):
        claim.validate(PROFILES['animation'])
        self.bridge._lease(claim)
        row = self.common.db.execute('SELECT * FROM resident_resource_grants WHERE grant_id=?',
                                    (claim.resource_grant,)).fetchone()
        if row['worker_id'] != worker or row['resource'] != 'video-global' or row['released']:
            raise ValueError('RETAINED_EXECUTION_OWNER_MISMATCH')
        # Existing request authority checks current attempt/fence/lease expiry.
        if live:
            self.bridge.authorize_publication(claim)
        elif str(self.common.row(claim.request_id)['fence']) != claim.fencing_token:
            raise ValueError('RETAINED_CURRENT_FENCE_CHANGED')
        m = self.models._row(model)
        if (model.role != 'animation' or model.worker_id != worker
                or m['expires_at'] <= self.common.clock() or self.trusted_worker(worker) is not True):
            raise ValueError('RETAINED_MODEL_OWNER_MISMATCH')

    def register(self, claim, model, worker, engine, prompt):
        import re
        if (set(engine) != {'pid','pgid','createTime'} or type(engine['pid']) is not int
                or engine['pid'] <= 1 or engine['pgid'] != engine['pid']
                or type(engine['createTime']) not in (int,float)
                or not math.isfinite(engine['createTime']) or engine['createTime'] <= 0
                or not isinstance(prompt,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', prompt)):
            raise ValueError('RETAINED_IDENTITY_INVALID')
        with self.bridge.transaction():
            self._current(claim, model, worker)
            encoded = json.dumps(engine, sort_keys=True)
            old = self.common.db.execute('SELECT * FROM resident_animation_sessions WHERE grant_id=?',
                                        (claim.resource_grant,)).fetchone()
            prompts = []
            if old:
                if (old['sealed'] or old['model_json'] != canonical(model)
                        or old['worker_id'] != worker or old['engine_json'] != encoded):
                    raise ValueError('RETAINED_BINDING_CHANGED')
                prompts = json.loads(old['prompts_json'])
            if prompt not in prompts:
                prompts.append(prompt)
            if len(prompts) > 16:
                raise ValueError('RETAINED_PROMPT_LIMIT')
            self.common.db.execute('INSERT OR REPLACE INTO resident_animation_sessions VALUES(?,?,?,?,?,0)',
                (claim.resource_grant,canonical(model),worker,encoded,json.dumps(prompts)))

    def seal(self, claim, model, worker, terminal_prompt):
        with self.bridge.transaction():
            self._current(claim, model, worker)
            row = self.common.db.execute('SELECT * FROM resident_animation_sessions WHERE grant_id=?',
                                        (claim.resource_grant,)).fetchone()
            if (not row or row['model_json'] != canonical(model) or row['worker_id'] != worker
                    or json.loads(row['prompts_json'])[-1] != terminal_prompt):
                raise ValueError('RETAINED_TERMINAL_MISMATCH')
            self.common.db.execute('UPDATE resident_animation_sessions SET sealed=1 WHERE grant_id=?',
                                   (claim.resource_grant,))

    def _historical_fence(self, claim):
        current = self.common.row(claim.request_id)
        if str(current['fence']) == claim.fencing_token:return True
        # Only the service-owned finalization lease may advance this attempt.
        exists = self.common.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='resident_reconciliations'").fetchone()
        if not exists:return False
        row = self.common.db.execute('SELECT recovery_json FROM resident_reconciliations WHERE grant_id=?', (claim.resource_grant,)).fetchone()
        recovery = json.loads(row[0]) if row and row[0] else None
        return bool(recovery and current['fence'] == recovery['fence'] and
            (not current['worker'] or (current['worker'] == recovery['workerId'] and current['nonce'] == recovery['nonce'])))

    def _joined_after_idle(self, claim, row):
        """A stopped retained model can finish a still-reserved publication.

        Requires the prior fresh idle receipt AND the real exact-model joined
        proof covering its registered engine, not merely a released memory row.
        """
        from ..terminal_evidence import scope_key
        receipt = self.common.db.execute('SELECT session_json FROM resident_animation_idle_receipts WHERE grant_id=?', (claim.resource_grant,)).fetchone()
        if not receipt or receipt[0] != json.dumps(dict(row), sort_keys=True) or not self._historical_fence(claim):return False
        lease = ModelLease(**json.loads(row['model_json']))
        self.models._row(lease, allow_released=True)
        grant = self.common.db.execute('SELECT worker_id,resource FROM resident_resource_grants WHERE grant_id=?', (claim.resource_grant,)).fetchone()
        if lease.worker_id != row['worker_id'] or lease.role != 'animation' or grant['worker_id'] != row['worker_id'] or grant['resource'] != 'video-global':return False
        exists = self.common.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='resident_terminal_evidence'").fetchone()
        if not exists:return False
        proof = self.common.db.execute("SELECT evidence_json FROM resident_terminal_evidence WHERE scope_hash=? AND scope_kind='model'", (scope_key(lease),)).fetchone()
        if not proof:return False
        evidence = json.loads(proof[0]);engine = json.loads(row['engine_json'])
        if evidence.get('outcome') != 'joined' or not any(p.get('pid') == engine['pid'] and p.get('pgid') == engine['pgid'] for p in evidence.get('processes',[])):return False
        return self.models.verify_unloaded(lease) is True

    def verify_idle(self, claim):
        try:
            with self.bridge.transaction():
                row = self.common.db.execute('SELECT * FROM resident_animation_sessions WHERE grant_id=?',
                                            (claim.resource_grant,)).fetchone()
                if not row or not row['sealed']:
                    return False
                self.bridge._lease(claim, allow_released=True)
                grant = self.common.db.execute('SELECT released FROM resident_resource_grants WHERE grant_id=?', (claim.resource_grant,)).fetchone()
                if grant and grant['released']:
                    receipt = self.common.db.execute('SELECT session_json FROM resident_animation_idle_receipts WHERE grant_id=?', (claim.resource_grant,)).fetchone()
                    return bool(receipt and receipt['session_json'] == json.dumps(dict(row), sort_keys=True)
                        and self._historical_fence(claim))
                if self._joined_after_idle(claim,row):return True
                self._current(claim, ModelLease(**json.loads(row['model_json'])), row['worker_id'], live=False)
                snapshot = dict(row)
            if self.probe(json.loads(snapshot['engine_json']), json.loads(snapshot['prompts_json'])) is not True:
                return False
            with self.bridge.transaction():
                current = self.common.db.execute('SELECT * FROM resident_animation_sessions WHERE grant_id=?', (claim.resource_grant,)).fetchone()
                if not current or dict(current) != snapshot:return False
                self._current(claim, ModelLease(**json.loads(current['model_json'])), current['worker_id'], live=False)
                encoded = json.dumps(dict(current), sort_keys=True)
                self.common.db.execute('INSERT OR IGNORE INTO resident_animation_idle_receipts VALUES(?,?)', (claim.resource_grant,encoded))
                receipt = self.common.db.execute('SELECT session_json FROM resident_animation_idle_receipts WHERE grant_id=?', (claim.resource_grant,)).fetchone()
                return receipt['session_json'] == encoded
        except Exception:
            return False

    def verify_report(self, claim, worker, hook):
        with self.bridge.transaction():
            row = self.common.db.execute('SELECT * FROM resident_animation_sessions WHERE grant_id=?',
                                        (claim.resource_grant,)).fetchone()
            if (not row or row['worker_id'] != worker or not isinstance(hook,dict)
                    or hook.get('outcome') != 'retained_idle'
                    or hook.get('engine') != json.loads(row['engine_json'])
                    or hook.get('modelGrantId') != json.loads(row['model_json'])['grant_id']
                    or hook.get('terminal',{}).get('promptId') != json.loads(row['prompts_json'])[-1]):
                raise ValueError('RETAINED_REPORT_MISMATCH')
        if self.verify_idle(claim) is not True:
            raise ValueError('RETAINED_IDLE_NOT_VERIFIED')
