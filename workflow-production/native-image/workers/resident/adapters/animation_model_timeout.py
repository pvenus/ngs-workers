"""Management-only expired model closure, distinct from historical wait/exit proof."""
from dataclasses import asdict
import json
import math
from .attempt_liveness import digest, timeout
from ..contracts import Claim
from .model_residency import ModelLease, canonical


class AnimationModelTimeout:
    def __init__(self, disposition, models, observe):
        self.disposition, self.models, self.observe = disposition, models, observe
        self.bridge = disposition.bridge

    @property
    def db(self):
        return self.disposition.common.db

    def initialize_schema(self):
        with self.bridge.transaction():
            self.db.execute('CREATE TABLE IF NOT EXISTS resident_animation_model_timeout_closures(grant_id TEXT PRIMARY KEY,receipt_json TEXT NOT NULL)')

    @staticmethod
    def _need(value):
        if not value:
            raise ValueError('ANIMATION_MODEL_TIMEOUT_UNVERIFIED')

    def _state(self, claim, model):
        state = self.disposition._state(claim, check_model=False)
        self._need(type(model) is ModelLease and model.role == 'animation' and model.worker_id == state['workerId'])
        row = dict(self.models._row(model))
        self._need(row['expires_at'] <= self.disposition.common.clock())
        outstanding = self.db.execute("SELECT grant_id FROM resident_model_leases WHERE role='animation' AND released=0").fetchall()
        self._need([r[0] for r in outstanding] == [model.grant_id])
        active = self.db.execute("SELECT grant_id FROM resident_resource_grants WHERE resource='video-global' AND released=0").fetchall()
        self._need([r[0] for r in active] == [claim.resource_grant])
        engine, links = self._links(model)
        return state, row, engine, links

    def _links(self, model):
        links = []
        for session in self.db.execute('SELECT * FROM resident_animation_sessions WHERE model_json=? ORDER BY grant_id',(canonical(model),)):
            grant = self.db.execute('SELECT request_id,worker_id,resource,released FROM resident_resource_grants WHERE grant_id=?',(session['grant_id'],)).fetchone()
            idle = self.db.execute('SELECT session_json FROM resident_animation_idle_receipts WHERE grant_id=?',(session['grant_id'],)).fetchone()
            self._need(grant is not None and grant['worker_id']==model.worker_id and grant['resource']=='video-global' and grant['released']==1)
            finished = self.disposition.common.row(grant['request_id'])
            self._need(finished['state']=='completed' and finished['worker'] is None and bool(finished['publication_receipt']))
            self._need(session['worker_id']==model.worker_id and session['sealed']==1 and idle is not None and idle[0]==json.dumps(dict(session),sort_keys=True))
            links.append({'session':dict(session),'requestId':grant['request_id'],'fence':finished['fence']})
        self._need(bool(links))
        engine = json.loads(links[0]['session']['engine_json'])
        self._need(set(engine)=={'pid','pgid','createTime'} and type(engine['pid']) is int and engine['pid']>1 and engine['pgid']==engine['pid'] and type(engine['createTime']) in (int,float) and math.isfinite(engine['createTime']) and engine['createTime']>0)
        self._need(all(json.loads(x['session']['engine_json'])==engine for x in links))
        return engine, links

    def close(self, claim, model):
        """Call only after normal shutdown; never signals or waits for a process."""
        with self.bridge.transaction():
            old = self.receipt(claim, model)
            if old is not None:
                return old
            state, row, engine, links = self._state(claim,model)
        d = self.disposition
        first = d._observation(claim,state['workerId'],observe=lambda c:self.observe(c,engine=engine))
        second = d._observation(claim,state['workerId'],observe=lambda c:self.observe(c,engine=engine))
        self._need({k:v for k,v in first.items() if k!='observed_at'}=={k:v for k,v in second.items() if k!='observed_at'} and first['observed_at']<=second['observed_at'])
        with self.bridge.transaction():
            self._need(self._state(claim,model)==(state,row,engine,links) and all(0<=d.clock()-o['observed_at']<=d.max_age for o in (first,second)))
            receipt = {'schemaVersion':'resident.animation-model-timeout-closure.v1','scopeHash':digest(asdict(claim)),
                       'leaseHash':digest(asdict(model)),'grantId':model.grant_id,'engine':engine,
                       'taskGrantId':claim.resource_grant,'sessionBindingHash':digest(links),'timeoutHash':state['timeoutHash'],
                       'observations':[first,second],'historicalModelOutcome':'unknown',
                       'processAbsenceObserved':True,'reservationReleased':True}
            receipt['receiptHash'] = digest(receipt)
            self.db.execute('INSERT INTO resident_animation_model_timeout_closures VALUES(?,?)',(model.grant_id,json.dumps(receipt,sort_keys=True)))
            updated=self.db.execute('UPDATE resident_model_leases SET released=1 WHERE grant_id=? AND released=0 AND lease_json=? AND expires_at=?',(model.grant_id,canonical(model),row['expires_at']))
            self._need(updated.rowcount==1)
            return receipt

    def _idle_state(self, model):
        self._need(type(model) is ModelLease and model.role == 'animation')
        row = dict(self.models._row(model))
        self._need(row['expires_at'] <= self.disposition.common.clock())
        outstanding = self.db.execute("SELECT grant_id FROM resident_model_leases WHERE role='animation' AND released=0").fetchall()
        self._need([r[0] for r in outstanding] == [model.grant_id])
        self._need(self.db.execute("SELECT 1 FROM resident_resource_grants WHERE resource='video-global' AND released=0").fetchone() is None)
        self._need(self.db.execute("SELECT 1 FROM common_requests WHERE state IN ('processing','synchronizing') AND (request_type='npc.animation.generate' OR worker=?)",(model.worker_id,)).fetchone() is None)
        engine, links = self._links(model)
        # This completed task is an observation context, not a timeout target.
        raw=self.db.execute('SELECT claim_json FROM resident_resource_grants WHERE grant_id=?',(links[0]['session']['grant_id'],)).fetchone()
        body=json.loads(raw[0]);body['input_refs']=tuple(body['input_refs']);context=Claim(**body)
        issued=self.bridge._issued_lease(context,allow_released=True)
        self._need(context.request_id==links[0]['requestId'] and context.resource_grant==links[0]['session']['grant_id']
                   and context.capability=='animation.generate' and issued['workerId']==model.worker_id)
        return row, engine, links, context

    def close_idle(self, model):
        """Release an expired idle model, with no task closure or historical exit claim."""
        with self.bridge.transaction():
            old=self.historical_receipt(model)
            if old is not None:
                self._need(old['schemaVersion']=='resident.animation-idle-model-timeout-closure.v1')
                return old
            row, engine, links, context = self._idle_state(model)
        d=self.disposition
        first=d._observation(context,model.worker_id,observe=lambda c:self.observe(c,engine=engine))
        second=d._observation(context,model.worker_id,observe=lambda c:self.observe(c,engine=engine))
        self._need({k:v for k,v in first.items()if k!='observed_at'}=={k:v for k,v in second.items()if k!='observed_at'} and first['observed_at']<=second['observed_at'])
        with self.bridge.transaction():
            self._need(self._idle_state(model)==(row,engine,links,context) and all(0<=d.clock()-o['observed_at']<=d.max_age for o in (first,second)))
            receipt={'schemaVersion':'resident.animation-idle-model-timeout-closure.v1','leaseHash':digest(asdict(model)),
                     'grantId':model.grant_id,'engine':engine,'sessionBindingHash':digest(links),
                     'observations':[first,second],'historicalModelOutcome':'unknown',
                     'processAbsenceObserved':True,'reservationReleased':True}
            receipt['receiptHash']=digest(receipt)
            self.db.execute('INSERT INTO resident_animation_model_timeout_closures VALUES(?,?)',(model.grant_id,json.dumps(receipt,sort_keys=True)))
            result=self.db.execute('UPDATE resident_model_leases SET released=1 WHERE grant_id=? AND released=0 AND lease_json=? AND expires_at=?',(model.grant_id,canonical(model),row['expires_at']))
            self._need(result.rowcount==1)
            return receipt

    def historical_receipt(self, model):
        row=self.db.execute('SELECT receipt_json FROM resident_animation_model_timeout_closures WHERE grant_id=?',(model.grant_id,)).fetchone()
        if row is None:return None
        p=json.loads(row[0])
        self._need(self.models._row(model,allow_released=True)['released']==1 and p['schemaVersion'] in ('resident.animation-model-timeout-closure.v1','resident.animation-idle-model-timeout-closure.v1')
                   and p['grantId']==model.grant_id and p['leaseHash']==digest(asdict(model))
                   and p['historicalModelOutcome']=='unknown' and p['processAbsenceObserved'] is True and p['reservationReleased'] is True
                   and p['receiptHash']==digest({k:v for k,v in p.items() if k!='receiptHash'}))
        if p['schemaVersion']=='resident.animation-idle-model-timeout-closure.v1':
            engine, links = self._links(model)
            self._need(p['engine']==engine and p['sessionBindingHash']==digest(links))
            return p
        task=self.db.execute('SELECT claim_json FROM resident_resource_grants WHERE grant_id=?',(p['taskGrantId'],)).fetchone()
        self._need(task is not None)
        raw=json.loads(task[0]);raw['input_refs']=tuple(raw['input_refs']);original=Claim(**raw)
        issued=self.bridge._issued_lease(original,allow_released=True)
        proof=timeout(self.disposition.common,original)
        self._need(original.resource_grant==p['taskGrantId'] and original.capability=='animation.generate'
                   and issued['workerId']==model.worker_id and p['scopeHash']==digest(asdict(original))
                   and proof is not None and proof.get('expired') is True and p['timeoutHash']==digest(proof))
        return p

    def receipt(self, claim, model):
        p=self.historical_receipt(model)
        if p is not None:self._need(p['schemaVersion']=='resident.animation-model-timeout-closure.v1' and p['scopeHash']==digest(asdict(claim)))
        return p
