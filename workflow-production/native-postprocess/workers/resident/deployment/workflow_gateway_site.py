"""Request-side binding to ORIGINAL one-stop intake and child relationship tables.

No alternate intake, actor override, source writes or worker calls. The host must
supply a pinned original service factory and an exclusive route-owner predicate.
"""
import json
from ..adapters.workflow_gateway import WorkflowGateway
from .request_selector import digest


def build_workflow_gateway(common,lock,*,service_for_parent,formal_adapter,
                           validate_plan,executor_id,owns_parent):
    if not executor_id or not all(callable(p)for p in (service_for_parent,validate_plan,owns_parent)):
        raise ValueError('WORKFLOW_SITE_BINDINGS_REQUIRED')

    def load(rid,executor):
        if executor!=executor_id:raise ValueError('WORKFLOW_EXECUTOR_DENIED')
        row=dict(common.row(rid))
        if (row['request_type']!='npc.workflow' or row['source']!='npc-one-stop'
            or owns_parent(row)is not True):raise ValueError('WORKFLOW_PARENT_NOT_OWNED')
        source=common.db.execute('SELECT * FROM npc_one_stop_requests WHERE request_id=?',(rid,)).fetchone()
        saved=common.db.execute('SELECT * FROM npc_one_stop_plans WHERE parent_id=?',(rid,)).fetchone()
        refs=common.db.execute("SELECT * FROM common_payload_refs WHERE request_id=? AND domain='npc-one-stop' AND schema_version='ngs.npc-one-stop.v1'",(rid,)).fetchall()
        if source is None or saved is None or len(refs)!=1:raise ValueError('WORKFLOW_ORIGINAL_PARENT_REQUIRED')
        request,plan=json.loads(source['body']),json.loads(saved['plan'])
        if (source['actor']!=row['requester'] or refs[0]['reference_id']!=rid
            or source['input_hash']!=digest(request) or refs[0]['input_hash']!=source['input_hash']
            or saved['plan_hash']!=digest(plan)):
            raise ValueError('WORKFLOW_ORIGINAL_PARENT_CHANGED')
        validate_plan(plan,request)
        value={'requestId':rid,'actor':row['requester'],'request':request,
                'requestHash':source['input_hash'],'plan':plan,'planHash':saved['plan_hash']}
        from .workflow_retry_proof import approved_retries
        value['approvedRetries']=approved_retries(common,value)
        return value

    def list_parents(executor):
        if executor!=executor_id:raise ValueError('WORKFLOW_EXECUTOR_DENIED')
        rows=common.db.execute("SELECT * FROM common_requests WHERE request_type='npc.workflow' AND source='npc-one-stop' AND state IN ('queued','retry_wait','processing') ORDER BY created_at,request_id").fetchall()
        return [r['request_id']for r in rows if owns_parent(dict(r))is True]

    def service(parent):
        value=service_for_parent(parent['requestId'])
        if value.common is not common:raise ValueError('WORKFLOW_SERVICE_AUTHORITY_CHANGED')
        return value

    def register(parent,stage,key,child):
        rid=parent['requestId'];source=service(parent)
        # Original per-parent OS lock also excludes accidental concurrent legacy
        # tick. Deployment ownership is still mandatory, not replaced by this lock.
        with source.lock(rid),common.tx():
            state=common.row(rid)['state']
            if state not in ('queued','retry_wait','processing'):raise ValueError('WORKFLOW_PARENT_NOT_EXECUTABLE')
            rows=common.db.execute('SELECT * FROM npc_one_stop_attempts WHERE parent_id=? AND stage_id=? ORDER BY attempt DESC',(rid,stage)).fetchall()
            if not rows:
                common.db.execute('INSERT INTO npc_one_stop_attempts(parent_id,stage_id,attempt,child_key) VALUES(?,?,1,?)',(rid,stage,key))
            else:
                current=rows[0];retry=parent.get('approvedRetries',{}).get(stage)
                if current['child_key']!=key or (current['attempt']!=1 and (not retry or retry['attempt']!=current['attempt']or retry['childKey']!=key)):
                    raise ValueError('WORKFLOW_LEGACY_ATTEMPT_HANDOFF_REQUIRED')
            row=common.db.execute('SELECT * FROM npc_one_stop_attempts WHERE parent_id=? AND stage_id=? ORDER BY attempt DESC LIMIT 1',(rid,stage)).fetchone()
            if child is not None:
                if row['child_id']is not None and (row['child_id']!=child['requestId']or row['channel']!=child['channel']):
                    raise ValueError('WORKFLOW_ORIGINAL_CHILD_CHANGED')
                original=dict(common.row(child['requestId']))
                if original['requester']!=parent['actor']:raise ValueError('WORKFLOW_CHILD_ACTOR_CHANGED')
                common.db.execute('UPDATE npc_one_stop_attempts SET child_id=?,channel=?,error=NULL WHERE parent_id=? AND stage_id=? AND attempt=?',(child['requestId'],child['channel'],rid,stage,row['attempt']))
            if state!='processing':source.transition(rid,'processing')

    def observe(parent,stage,value):
        rid=parent['requestId'];source=service(parent)
        with source.lock(rid),common.tx():
            row=common.db.execute('SELECT * FROM npc_one_stop_attempts WHERE parent_id=? AND stage_id=? ORDER BY attempt DESC LIMIT 1',(rid,stage)).fetchone()
            if row is None or row['child_id']!=value['requestId']:raise ValueError('WORKFLOW_ORIGINAL_CHILD_REQUIRED')
            state=common.row(rid)['state']
            if value['state']=='published':
                receipt=value['receipt'];encoded=json.dumps(receipt,ensure_ascii=False,sort_keys=True,separators=(',',':'))
                if row['receipt']is not None and json.loads(row['receipt'])!=receipt:raise ValueError('WORKFLOW_ORIGINAL_RECEIPT_CHANGED')
                common.db.execute('UPDATE npc_one_stop_attempts SET receipt=?,error=NULL,failure_count=0 WHERE parent_id=? AND stage_id=? AND attempt=?',(encoded,rid,stage,row['attempt']))
                receipts={r['stage_id']:json.loads(r['receipt'])for r in common.db.execute('SELECT stage_id,receipt FROM npc_one_stop_attempts WHERE parent_id=? AND receipt IS NOT NULL',(rid,))}
                stages=[s['id']for s in parent['plan']['stages']]
                if all(s in receipts for s in stages)and state!='completed':
                    if state!='processing':raise ValueError('WORKFLOW_PARENT_COMPLETION_STATE_CHANGED')
                    publication={'requestId':rid,'verified':True,'results':[receipts[s]for s in stages]}
                    source.transition(rid,'completed',receipt=publication,publication=publication)
            elif value['state']=='failed':
                common.db.execute('UPDATE npc_one_stop_attempts SET error=? WHERE parent_id=? AND stage_id=? AND attempt=?',(str(value.get('error')or'CHILD_FAILED')[:2000],rid,stage,row['attempt']))
                if state not in ('blocked','failed','conflict','discarded'):
                    source.transition(rid,'blocked',error='ONE_STOP_CHILD_FAILED',details={'stageId':stage,'childId':value['requestId']})
            elif value['state']!='pending':raise ValueError('WORKFLOW_OBSERVATION_STATE_INVALID')

    return WorkflowGateway(common,lock,load_parent=load,list_parents=list_parents,
        formal_adapter=formal_adapter,register_child=register,observe_child=observe)


def _gateway_from_pinned_sources(sources,common,lock,*,one_stop_root,executor_id,owns_parent,formal_adapter_factory=None):
    """Compose original adapters with pinned source modules and existing root only.

    Call inside Request ThreadAuthority scope. This is not a source selector or
    bootstrap: original intake tables and existing original root are required.
    """
    from pathlib import Path
    root=Path(one_stop_root)
    if not root.is_absolute()or root.is_symlink()or not root.is_dir()or root.resolve()!=root:
        raise ValueError('WORKFLOW_ORIGINAL_ROOT_REQUIRED')
    children=root/'children'
    if children.is_symlink()or not children.is_dir():raise ValueError('WORKFLOW_ORIGINAL_CHILD_ROOT_REQUIRED')
    required={'npc_one_stop_requests','npc_one_stop_plans','npc_one_stop_attempts','npc_one_stop_retries'}
    present={r[0]for r in common.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not required<=present:raise ValueError('WORKFLOW_ORIGINAL_SCHEMA_REQUIRED')
    modules=sources.modules
    if formal_adapter_factory is None:adapter=modules['one_stop_adapters'].FormalAdapters(children)
    else:
        if not callable(formal_adapter_factory):raise ValueError('WORKFLOW_FORMAL_FACTORY_REQUIRED')
        adapter=formal_adapter_factory(children)
        if not isinstance(adapter,modules['one_stop_adapters'].FormalAdapters):raise ValueError('WORKFLOW_FORMAL_FACTORY_REQUIRED')
    def service(rid):
        # Original service only registers/observes child requests; the gateway
        # invokes its lock/transition, never tick or a worker execution method.
        return modules['one_stop_service'].OneStop(common,root,adapter,
            selector=lambda request:(_ for _ in ()).throw(ValueError('WORKFLOW_NEW_PLAN_FORBIDDEN')))
    return build_workflow_gateway(common,lock,service_for_parent=service,
        formal_adapter=adapter,validate_plan=modules['one_stop_plan'].validate,
        executor_id=executor_id,owns_parent=owns_parent)


def gateway_from_pinned_sources(sources,common,lock,*,one_stop_root,executor_id,owns_parent,formal_adapter_factory=None):
    """Inert proxy: construct/validate only inside each Request thread scope.

    Never cache a SQLite-bound original service across threads. Initialization
    and all reads/writes remain explicit method calls under the host's scope.
    """
    def build():
        return _gateway_from_pinned_sources(sources,common,lock,
            one_stop_root=one_stop_root,executor_id=executor_id,owns_parent=owns_parent,formal_adapter_factory=formal_adapter_factory)
    class LazyGateway:
        def initialize_schema(self):return build().initialize_schema()
        def runs(self,*args):return build().runs(*args)
        def read(self,*args):return build().read(*args)
        def submit(self,*args):return build().submit(*args)
        def view(self,*args):return build().view(*args)
    return LazyGateway()


def retry_loop_from_pinned_sources(sources,common,*,one_stop_root,owns_parent,logical_retry_reader=None,settled_retry_reader=None):
    """Inert loop binding. Actual original service is constructed in tick scope."""
    from pathlib import Path
    from .workflow_retry_loop import WorkflowRetryLoop
    holder={}
    def service(rid):
        root=Path(one_stop_root);children=root/'children'
        if (not root.is_absolute()or root.is_symlink()or root.resolve()!=root or not root.is_dir()
            or children.is_symlink()or not children.is_dir()):raise ValueError('WORKFLOW_ORIGINAL_ROOT_REQUIRED')
        reader=getattr(holder['loop'],'logical_retry_reader',None)
        settled=getattr(holder['loop'],'settled_retry_reader',None)
        options={}
        if reader is not None:options['logical_retry_reader']=reader
        if settled is not None:options['settled_retry_reader']=settled
        adapter=sources.modules['one_stop_adapters'].FormalAdapters(children,**options)
        return sources.modules['one_stop_service'].OneStop(common,root,adapter,
            selector=lambda request:(_ for _ in ()).throw(ValueError('WORKFLOW_NEW_PLAN_FORBIDDEN')))
    loop=WorkflowRetryLoop(common,service,sources.modules['one_stop_retry'].work,owns_parent=owns_parent)
    holder['loop']=loop
    if logical_retry_reader is not None:loop.bind_logical_retry_reader(logical_retry_reader)
    if settled_retry_reader is not None:loop.bind_settled_retry_reader(settled_retry_reader)
    return loop
