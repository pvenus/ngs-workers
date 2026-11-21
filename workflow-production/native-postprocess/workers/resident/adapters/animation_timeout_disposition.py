"""Explicit server-only timeout closure after trusted service/OS quiescence.
No historical execution receipt is synthesized. No worker-facing endpoint.
"""
from dataclasses import asdict, dataclass
import json
from .attempt_liveness import digest, timeout

@dataclass(frozen=True)
class Quiescence:
    host_id: str
    boot_id: str
    service_identity: str
    worker_id: str
    observed_at: float
    service_stopped: bool
    desired_running: bool
    process_inventory_complete: bool
    matching_processes: tuple
    listening: bool

class AnimationTimeoutDisposition:
    def __init__(self, bridge, *, observe, model_reader, clock, max_age=5):
        self.bridge,self.common=bridge,bridge.common
        self.observe,self.model_reader,self.clock=observe,model_reader,clock
        if not 0<max_age<=5:raise ValueError('TIMEOUT_DISPOSITION_CONFIG')
        self.max_age=max_age
    def initialize_schema(self):
        with self.bridge.transaction():
            self.common.db.execute('CREATE TABLE IF NOT EXISTS resident_animation_timeout_dispositions(grant_id TEXT PRIMARY KEY,claim_json TEXT NOT NULL,receipt_json TEXT NOT NULL)')
    def _need(self,ok):
        if not ok:raise ValueError('ANIMATION_TIMEOUT_DISPOSITION_UNVERIFIED')
    def _state(self,c,*,check_model=True):
        lease=self.bridge._issued_lease(c,allow_released=True)
        row=dict(self.common.row(c.request_id));p=timeout(self.common,c)
        self._need(c.capability=='animation.generate' and self.bridge._capability(row,c.request_id)=='animation.generate')
        self._need(p is not None and p.get('expired')is True and p.get('scopeHash')==digest(asdict(c)))
        self._need(row['state']=='blocked' and row['fence']==lease['fence'] and row['worker']is None and row['lease_until']is None)
        self._need(not row.get('source_receipt') and not row.get('publication_receipt'))
        for table,key,value in [('resident_failure_tracking','request_id',c.request_id),('resident_failure_grants','grant_id',c.resource_grant)]:
            r=self.common.db.execute(f'SELECT publication_started FROM {table} WHERE {key}=?',(value,)).fetchone()
            self._need(r is not None and r[0]==0)
        for table in ('resident_publications','resident_publication_checkpoints'):
            self._need(self.common.db.execute(f'SELECT 1 FROM {table} WHERE grant_id=?',(c.resource_grant,)).fetchone()is None)
        models=[dict(r) for r in self.common.db.execute("SELECT * FROM resident_model_leases WHERE worker_id=? AND role='animation' ORDER BY grant_id",(lease['workerId'],))]
        if check_model:self._need(bool(models) and all(r['released']==1 for r in models))
        return {'row':row,'timeoutHash':digest(p),'modelStateHash':digest(models),'workerId':lease['workerId']}
    def _observation(self,c,worker,*,observe=None):
        o=(observe or self.observe)(c)
        self._need(isinstance(o,Quiescence) and all(isinstance(v,str)and v for v in (o.host_id,o.boot_id,o.service_identity)) and o.worker_id==worker)
        self._need(0<=self.clock()-o.observed_at<=self.max_age and o.service_stopped is True and o.desired_running is False and o.process_inventory_complete is True and o.matching_processes==() and o.listening is False)
        return json.loads(json.dumps(asdict(o)))
    def close(self,c):
        # Existing liveness expiry must already have closed replay/renew/publish.
        with self.bridge.transaction():
            old=self.common.db.execute('SELECT receipt_json FROM resident_animation_timeout_dispositions WHERE grant_id=?',(c.resource_grant,)).fetchone()
            if old:
                self._need(self.verify(c));return json.loads(old[0])
            state=self._state(c)
        model=self.model_reader(c)
        self._need(model.get('modelStateHash')==state['modelStateHash'] and model.get('allReleased')is True and model.get('activeCount')==0 and model.get('workerId')==state['workerId'] and model.get('role')=='animation' and bool(model.get('evidenceHash')))
        first=self._observation(c,state['workerId'])
        second=self._observation(c,state['workerId'])
        self._need({k:v for k,v in first.items()if k!='observed_at'}=={k:v for k,v in second.items()if k!='observed_at'} and second['observed_at']>=first['observed_at'])
        with self.bridge.transaction():
            self._need(self._state(c)==state and all(0<=self.clock()-o['observed_at']<=self.max_age for o in (first,second)))
            receipt={'schemaVersion':'resident.animation-timeout-disposition.v1','evidenceKind':'timeout-service-quiescent','scopeHash':digest(asdict(c)),'requestId':c.request_id,'grantId':c.resource_grant,'timeoutHash':state['timeoutHash'],'requestStateHash':digest(state['row']),'modelEvidenceHash':digest(model),'observations':[first,second],'historicalExecutionOutcome':'unknown','processAbsenceObserved':True,'resourceReleased':True,'artifactsPreserved':True}
            receipt['receiptHash']=digest(receipt)
            self.common.db.execute('INSERT INTO resident_animation_timeout_dispositions VALUES(?,?,?)',(c.resource_grant,json.dumps(asdict(c),sort_keys=True),json.dumps(receipt,sort_keys=True)))
            self.common.db.execute('UPDATE resident_resource_grants SET released=1 WHERE grant_id=? AND released=0',(c.resource_grant,))
            return receipt
    def verify(self,c):
        try:
            with self.bridge.transaction():
                state=self._state(c,check_model=False)
                row=self.common.db.execute('SELECT * FROM resident_animation_timeout_dispositions WHERE grant_id=?',(c.resource_grant,)).fetchone()
                self._need(row is not None and row['claim_json']==json.dumps(asdict(c),sort_keys=True))
                p=json.loads(row['receipt_json'])
                self._need(p['receiptHash']==digest({k:v for k,v in p.items()if k!='receiptHash'}) and p['scopeHash']==digest(asdict(c)) and p['timeoutHash']==state['timeoutHash'] and p['requestStateHash']==digest(state['row']))
                return True
        except Exception:return False

    def receipt_hash(self,c):
        if not self.verify(c):raise ValueError('ANIMATION_TIMEOUT_DISPOSITION_UNVERIFIED')
        return json.loads(self.common.db.execute('SELECT receipt_json FROM resident_animation_timeout_dispositions WHERE grant_id=?',(c.resource_grant,)).fetchone()[0])['receiptHash']

class ReleasedAnimationModels:
    """Whole worker model ledger, NOT an inferred request-to-model association."""
    def __init__(self, bridge, terminal, model_timeout=None):self.bridge,self.terminal,self.model_timeout=bridge,terminal,model_timeout
    def __call__(self,c):
        from .model_residency import ModelLease
        from ..terminal_evidence import scope_key
        worker=self.bridge._issued_lease(c,allow_released=True)['workerId']
        db=self.bridge.common.db
        rows=db.execute("SELECT * FROM resident_model_leases WHERE worker_id=? AND role='animation' ORDER BY grant_id",(worker,)).fetchall()
        if not rows or any(r['released']!=1 for r in rows):raise ValueError('ANIMATION_MODEL_NOT_RELEASED')
        proofs=[];never=False
        for row in rows:
            lease=ModelLease(**json.loads(row['lease_json']))
            raw=db.execute("SELECT evidence_json FROM resident_terminal_evidence WHERE scope_hash=? AND scope_kind='model'",(scope_key(lease),)).fetchone()
            if raw is None or self.terminal.verify_unloaded(lease)is not True:
                closed=self.model_timeout.historical_receipt(lease) if self.model_timeout is not None else None
                if closed is None:raise ValueError('ANIMATION_MODEL_UNLOAD_UNVERIFIED')
                proofs.append({'grantId':lease.grant_id,'leaseHash':digest(asdict(lease)),'dispositionHash':closed['receiptHash']})
                continue
            evidence=json.loads(raw[0]);never=never or evidence.get('outcome')=='never_started'
            proofs.append({'grantId':lease.grant_id,'leaseHash':digest(json.loads(row['lease_json'])),'evidenceHash':digest(evidence)})
        if not never:raise ValueError('ANIMATION_MODEL_NO_START_EVIDENCE_MISSING')
        return {'workerId':worker,'role':'animation','allReleased':True,'activeCount':0,'modelScopeOnly':True,'evidenceHash':digest(proofs),'modelStateHash':digest([dict(r) for r in rows])}
