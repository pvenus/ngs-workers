"""Independent workflow execution state; domain request DB is never opened here.

Request ports must be authenticated official API clients. A step's idempotency
key is durable before submit. Unknown responses are observed/retried with that
same key, never replaced. Only verified child publication unlocks dependencies.
"""
import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from threading import RLock


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def validate_plan(plan, request):
    if plan.get('requestHash')!=digest(request) or plan.get('modelCalls')!=0 or plan.get('workerCalls')!=0:
        raise ValueError('WORKFLOW_PLAN_BINDING_INVALID')
    stages=plan.get('stages')
    if not isinstance(stages,list) or not stages or digest(stages)!=plan.get('planHash'):
        raise ValueError('WORKFLOW_PLAN_HASH_INVALID')
    seen=set()
    for stage in stages:
        if (not isinstance(stage,dict) or set(stage)!={'id','dependsOn','completion'}
            or not isinstance(stage['id'],str) or not stage['id'] or stage['id'] in seen
            or stage['completion']!='verified-publication' or not isinstance(stage['dependsOn'],list)
            or len(set(stage['dependsOn']))!=len(stage['dependsOn'])
            or any(dep not in seen for dep in stage['dependsOn'])):
            raise ValueError('WORKFLOW_PLAN_DAG_INVALID')
        seen.add(stage['id'])


class WorkflowExecutor:
    def __init__(self,path,requests,*,exclusive_owner,authorize_run):
        # This is a NEW workflow-only database, never the Request authority DB.
        self.path=Path(path)
        if self.path.is_symlink():raise ValueError('WORKFLOW_DATABASE_SYMLINK')
        self.requests=requests;self.exclusive_owner=exclusive_owner;self.authorize_run=authorize_run;self.lock=RLock()
        if not all(callable(getattr(requests,k,None)) for k in ('submit','view')) or not callable(exclusive_owner) or not callable(authorize_run):
            raise ValueError('WORKFLOW_OFFICIAL_PORTS_REQUIRED')

    @contextmanager
    def connection(self):
        db=sqlite3.connect(self.path);db.row_factory=sqlite3.Row
        try:yield db
        finally:db.close()

    def initialize(self):
        with self.connection() as db:
            # Reject accidentally selected domain/Common authority databases.
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables-{'workflow_runs','workflow_steps','workflow_retry_history'}:raise ValueError('WORKFLOW_DATABASE_NOT_DEDICATED')
            db.executescript('''CREATE TABLE IF NOT EXISTS workflow_runs(id TEXT PRIMARY KEY, request TEXT NOT NULL, plan TEXT NOT NULL, binding TEXT NOT NULL, authorization TEXT NOT NULL, state TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS workflow_steps(run_id TEXT NOT NULL,id TEXT NOT NULL,step_key TEXT NOT NULL UNIQUE,state TEXT NOT NULL,child_id TEXT,receipt TEXT,error TEXT,PRIMARY KEY(run_id,id));
            CREATE TABLE IF NOT EXISTS workflow_retry_history(run_id TEXT NOT NULL,stage_id TEXT NOT NULL,retry_request_id TEXT NOT NULL,proof_hash TEXT NOT NULL,previous_json TEXT NOT NULL,PRIMARY KEY(run_id,stage_id,retry_request_id));''')

    def register(self,rid,request,plan):
        if not isinstance(rid,str)or not rid:raise ValueError('WORKFLOW_RUN_ID_REQUIRED')
        validate_plan(plan,request)
        authorization=self.authorize_run(rid,request,plan)
        if (not isinstance(authorization,dict) or not authorization.get('parentRequestId')
            or authorization.get('definitionHash')!=digest(plan)
            or authorization.get('requestHash')!=digest(request)):
            raise ValueError('WORKFLOW_PARENT_AUTHORIZATION_REQUIRED')
        binding=digest({'request':request,'plan':plan,'authorization':authorization})
        with self.lock,self.connection() as db,db:
            old=db.execute('SELECT binding FROM workflow_runs WHERE id=?',(rid,)).fetchone()
            if old:
                if old['binding']!=binding:raise ValueError('WORKFLOW_RUN_BINDING_CHANGED')
                return
            db.execute('INSERT INTO workflow_runs VALUES(?,?,?,?,?,?)',(rid,json.dumps(request),json.dumps(plan),binding,json.dumps(authorization),'queued'))
            for stage in plan['stages']:
                key='workflow.step.'+digest([rid,stage['id'],binding])
                db.execute('INSERT INTO workflow_steps VALUES(?,?,?,?,NULL,NULL,NULL)',(rid,stage['id'],key,'pending'))

    def tick(self,rid):
        # Deployment must prove this run is not simultaneously legacy-owned.
        if self.exclusive_owner(rid)is not True:raise ValueError('WORKFLOW_EXCLUSIVE_OWNER_REQUIRED')
        with self.lock,self.connection() as db:
            row=db.execute('SELECT * FROM workflow_runs WHERE id=?',(rid,)).fetchone()
            if row is None:raise ValueError('WORKFLOW_RUN_NOT_FOUND')
            if row['state']=='completed':return self.view(rid)
            if row['state']=='blocked':
                self._approved_retry(db,row)
                row=db.execute('SELECT * FROM workflow_runs WHERE id=?',(rid,)).fetchone()
                if row['state']=='blocked':return self.view(rid)
            request,plan=json.loads(row['request']),json.loads(row['plan'])
            validate_plan(plan,request)
            authorization=json.loads(row['authorization'])
            if row['binding']!=digest({'request':request,'plan':plan,'authorization':authorization}):raise ValueError('WORKFLOW_RUN_BINDING_CHANGED')
            with db:db.execute("UPDATE workflow_runs SET state='processing' WHERE id=?",(rid,))
            for stage in plan['stages']:
                steps={r['id']:dict(r) for r in db.execute('SELECT * FROM workflow_steps WHERE run_id=?',(rid,))}
                step=steps[stage['id']]
                if step['state']in ('completed','blocked') or any(steps[d]['state']!='completed' for d in stage['dependsOn']):continue
                try:
                    if step['child_id'] is None:
                        with db:db.execute("UPDATE workflow_steps SET state='submitting' WHERE run_id=? AND id=?",(rid,stage['id']))
                        receipts=[json.loads(s['receipt']) for s in steps.values() if s['receipt']]
                        result=self.requests.submit(run_id=rid,stage=stage['id'],request=request,idempotency_key=step['step_key'],receipts=receipts,authorization=authorization)
                        child=result.get('requestId')
                        if not isinstance(child,str)or not child:raise ValueError('WORKFLOW_CHILD_ID_REQUIRED')
                        with db:db.execute("UPDATE workflow_steps SET child_id=?,state='waiting',error=NULL WHERE run_id=? AND id=?",(child,rid,stage['id']))
                    else:
                        result=self.requests.view(run_id=rid,stage=stage['id'],child_id=step['child_id'],authorization=authorization)
                        if result.get('requestId')!=step['child_id']:raise ValueError('WORKFLOW_CHILD_BINDING_CHANGED')
                        if result.get('state') in ('failed','blocked','conflict','discarded'):
                            with db:
                                db.execute("UPDATE workflow_steps SET state='blocked',error=? WHERE run_id=? AND id=?",(str(result.get('error')or'CHILD_FAILED'),rid,stage['id']))
                                db.execute("UPDATE workflow_runs SET state='blocked' WHERE id=?",(rid,))
                            break
                        if result.get('state')=='completed':
                            receipt=result.get('publication')
                            if not isinstance(receipt,dict)or receipt.get('verified')is not True or receipt.get('requestId')!=step['child_id']:
                                raise ValueError('WORKFLOW_CHILD_PUBLICATION_REQUIRED')
                            with db:db.execute("UPDATE workflow_steps SET state='completed',receipt=?,error=NULL WHERE run_id=? AND id=?",(json.dumps(receipt),rid,stage['id']))
                except Exception as exc:
                    with db:db.execute('UPDATE workflow_steps SET error=? WHERE run_id=? AND id=?',(type(exc).__name__,rid,stage['id']))
                    # Unknown submission keeps its exact durable key; other independent
                    # branches can proceed, but dependencies cannot.
            with db:
                remaining=db.execute("SELECT count(*) FROM workflow_steps WHERE run_id=? AND state!='completed'",(rid,)).fetchone()[0]
                if not remaining:db.execute("UPDATE workflow_runs SET state='completed' WHERE id=?",(rid,))
            return self.view(rid)

    def _approved_retry(self,db,row):
        callback=getattr(self.requests,'approved_retries',None)
        if callback is None:return
        rid=row['id'];authorization=json.loads(row['authorization'])
        approved=callback(rid,authorization)
        for stage,retry in approved.items():
            step=db.execute('SELECT * FROM workflow_steps WHERE run_id=? AND id=?',(rid,stage)).fetchone()
            if step is None or step['state']=='completed':continue
            old=db.execute('SELECT * FROM workflow_retry_history WHERE run_id=? AND stage_id=? AND retry_request_id=?',(rid,stage,retry['retryRequestId'])).fetchone()
            if old:
                if old['proof_hash']!=retry['proofHash']:raise ValueError('WORKFLOW_RETRY_PROOF_CHANGED')
                continue
            if type(retry['attempt'])is not int or retry['attempt']<1 or retry['childKey']!=rid+'.'+stage+'.'+str(retry['attempt']):
                raise ValueError('WORKFLOW_RETRY_KEY_CHANGED')
            child=retry.get('childId')
            if child is not None and step['child_id']not in (None,child):raise ValueError('WORKFLOW_RETRY_CHILD_CHANGED')
            with db:
                db.execute('INSERT INTO workflow_retry_history VALUES(?,?,?,?,?)',(rid,stage,retry['retryRequestId'],retry['proofHash'],json.dumps(dict(step))))
                db.execute('UPDATE workflow_steps SET state=?,step_key=?,child_id=?,receipt=NULL,error=NULL WHERE run_id=? AND id=?',('waiting'if child else'pending',retry['childKey'],child,rid,stage))
        if not db.execute("SELECT 1 FROM workflow_steps WHERE run_id=? AND state='blocked'",(rid,)).fetchone():
            with db:db.execute("UPDATE workflow_runs SET state='queued' WHERE id=?",(rid,))

    def unfinished_runs(self):
        with self.connection() as db:
            return [{'runId':r['id'],'request':json.loads(r['request']),'plan':json.loads(r['plan'])}
                    for r in db.execute("SELECT * FROM workflow_runs WHERE state NOT IN ('completed','blocked') ORDER BY rowid")]

    def view(self,rid):
        with self.connection() as db:
            row=db.execute('SELECT id,state,binding FROM workflow_runs WHERE id=?',(rid,)).fetchone()
            if row is None:raise ValueError('WORKFLOW_RUN_NOT_FOUND')
            return {**dict(row),'steps':[dict(r)for r in db.execute('SELECT id,state,child_id,error FROM workflow_steps WHERE run_id=? ORDER BY rowid',(rid,))]}


class OfficialWorkflowPorts:
    """ID-only authenticated Request gateway; never sends actor/body/key overrides."""
    def __init__(self, gateway):self.gateway=gateway
    def authorize(self, rid, request, plan):
        binding={'parentRequestId':rid,'definitionHash':digest(plan),'requestHash':digest(request)}
        original=self.gateway.read(binding)
        if (original.get('requestId')!=rid or original.get('request')!=request
            or original.get('plan')!=plan or original.get('requestHash')!=binding['requestHash']
            or original.get('planHash')!=binding['definitionHash']):
            raise ValueError('WORKFLOW_AUTHORIZED_PARENT_CHANGED')
        return binding
    def approved_retries(self,rid,authorization):
        if authorization['parentRequestId']!=rid:raise ValueError('WORKFLOW_PARENT_CHANGED')
        return self.gateway.read(authorization).get('approvedRetries',{})
    def pending_runs(self):
        return [{'runId':v['requestId'],'request':v['request'],'plan':v['plan']} for v in self.gateway.runs()]
    def submit(self, *, run_id, stage, authorization, **unused):
        if authorization['parentRequestId']!=run_id:raise ValueError('WORKFLOW_PARENT_CHANGED')
        return self.gateway.submit(authorization,stage)
    def view(self, *, run_id, stage, child_id, authorization):
        if authorization['parentRequestId']!=run_id:raise ValueError('WORKFLOW_PARENT_CHANGED')
        raw=self.gateway.view(authorization,stage)
        if raw.get('requestId')!=child_id:raise ValueError('WORKFLOW_CHILD_CHANGED')
        state={'pending':'queued','failed':'failed','published':'completed'}.get(raw.get('state'))
        if state is None:raise ValueError('WORKFLOW_CHILD_STATE_INVALID')
        return {'requestId':child_id,'state':state,'publication':raw.get('receipt'),'error':raw.get('error')}
