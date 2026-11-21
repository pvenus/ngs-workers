"""Request-owned processing of ALREADY formally accepted retry commands only."""
import json
from threading import Event
from .request_selector import digest


class WorkflowRetryLoop:
    def __init__(self,common,service_for_parent,work_retry,*,owns_parent,poll_seconds=1):
        self.common,self.service_for_parent,self.work_retry=common,service_for_parent,work_retry
        self.owns_parent=owns_parent;self.poll_seconds=poll_seconds
        self.stop=Event();self.last_errors={};self.logical_retry_reader=None;self.settled_retry_reader=None
        if not all(callable(v)for v in (service_for_parent,work_retry,owns_parent)):
            raise ValueError('WORKFLOW_RETRY_PORTS_REQUIRED')
    def bind_logical_retry_reader(self,reader):
        if not callable(reader)or self.logical_retry_reader is not None:raise ValueError('LOGICAL_RETRY_READER_BINDING')
        self.logical_retry_reader=reader
    def bind_settled_retry_reader(self,reader):
        if not callable(reader)or self.settled_retry_reader is not None:raise ValueError("SETTLED_RETRY_READER_BINDING")
        self.settled_retry_reader=reader
    def tick(self):
        db=self.common.db
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='npc_one_stop_retry_requests'").fetchone():return []
        rows=db.execute("SELECT r.* FROM common_requests r WHERE request_type='npc.retry-one-stop' AND source='npc-one-stop-retry' AND state IN ('queued','retry_wait','processing') AND retry_at<=? ORDER BY created_at,request_id",(self.common.clock(),)).fetchall()
        results=[]
        for raw in rows:
            row=dict(raw);rid=row['request_id']
            try:
                native=db.execute('SELECT * FROM npc_one_stop_retry_requests WHERE id=?',(rid,)).fetchone()
                if native is None:raise ValueError('WORKFLOW_RETRY_NATIVE_REQUIRED')
                parent=dict(self.common.row(native['parent_id']))
                if self.owns_parent(parent)is not True:continue
                proof=json.loads(native['proof'])
                refs=db.execute('SELECT * FROM common_payload_refs WHERE request_id=?',(rid,)).fetchall()
                if (native['actor']!=row['requester'] or parent['requester']!=row['requester']
                    or native['command_key']!=row['idempotency_key'] or proof.get('parentRequestId')!=native['parent_id']
                    or proof.get('actor')!=row['requester'] or len(refs)!=1
                    or refs[0]['domain']!='npc-one-stop-retry' or refs[0]['schema_version']!='ngs.npc-one-stop-retry.v1'
                    or refs[0]['reference_id']!=rid or refs[0]['input_hash']!=digest(proof)):
                    raise ValueError('WORKFLOW_RETRY_BINDING_CHANGED')
                service=self.service_for_parent(native['parent_id'])
                if service.common is not self.common:raise ValueError('WORKFLOW_RETRY_AUTHORITY_CHANGED')
                result=self.work_retry(service,rid,row['requester'])
                results.append((rid,result['state']));self.last_errors.pop(rid,None)
            except BlockingIOError:continue
            except Exception as exc:self.last_errors[rid]=type(exc).__name__
        return results
    def serve(self):
        while not self.stop.is_set():
            try:self.tick()
            except Exception as exc:self.last_errors['loop']=type(exc).__name__
            self.stop.wait(self.poll_seconds)
