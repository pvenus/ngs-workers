"""Request-owned formal child gateway for an independent workflow executor.

The executor sends IDs, not actor credentials, prompts, receipt claims or arbitrary
child commands. Original parent input/plan and original FormalAdapters remain the
only source of child commands. No worker execution or production DB bootstrap.
"""
import json
from .frame_workspace import digest

class WorkflowGateway:
 def __init__(self,common,lock,*,load_parent,list_parents,formal_adapter,register_child,observe_child):
  if not all(callable(v) for v in (load_parent,list_parents,register_child,observe_child)):
   raise ValueError('WORKFLOW_AUTHORITY_PORTS_REQUIRED')
  self.common,self.lock=common,lock
  self.load_parent,self.list_parents,self.adapter=load_parent,list_parents,formal_adapter
  self.register_child,self.observe_child=register_child,observe_child
 def initialize_schema(self):
  self.common.db.execute('''CREATE TABLE IF NOT EXISTS resident_workflow_children(
   parent_id TEXT NOT NULL,stage_id TEXT NOT NULL,parent_hash TEXT NOT NULL,
   plan_hash TEXT NOT NULL,child_key TEXT NOT NULL UNIQUE,child_id TEXT,
   channel TEXT,receipt_json TEXT,PRIMARY KEY(parent_id,stage_id))''')
  self.common.db.execute('''CREATE TABLE IF NOT EXISTS resident_workflow_child_history(parent_id TEXT NOT NULL,stage_id TEXT NOT NULL,child_key TEXT NOT NULL,retry_request_id TEXT NOT NULL,record_json TEXT NOT NULL,PRIMARY KEY(parent_id,stage_id,child_key))''')
 def parent(self,rid,executor):
  value=self.load_parent(rid,executor)
  required={'requestId','actor','request','requestHash','plan','planHash'}
  if not isinstance(value,dict)or not required<=value.keys()or value['requestId']!=rid:
   raise ValueError('WORKFLOW_PARENT_AUTHORITY_REQUIRED')
  if digest(value['request'])!=value['requestHash']or digest(value['plan'])!=value['planHash']:
   raise ValueError('WORKFLOW_PARENT_BINDING_CHANGED')
  stages=value['plan'].get('stages',[]);ids=[s['id']for s in stages]
  if not ids or len(set(ids))!=len(ids) or any(not set(s.get('dependsOn',[]))<=set(ids)or s['id']in s.get('dependsOn',[])for s in stages):
   raise ValueError('WORKFLOW_PLAN_INVALID')
  done=set()
  while len(done)<len(ids):
   ready={s['id']for s in stages if set(s.get('dependsOn',[]))<=done}-done
   if not ready:raise ValueError('WORKFLOW_PLAN_CYCLE')
   done.update(ready)
  return value
 def runs(self,executor):
  return [self.parent(rid,executor)for rid in self.list_parents(executor)]
 def read(self,rid,executor):return self.parent(rid,executor)
 def _row(self,rid,stage):
  return self.common.db.execute('SELECT * FROM resident_workflow_children WHERE parent_id=? AND stage_id=?',(rid,stage)).fetchone()
 def _bound(self,row,parent):
  if row['parent_hash']!=parent['requestHash']or row['plan_hash']!=parent['planHash']:
   raise ValueError('WORKFLOW_SAVED_BINDING_CHANGED')
 def submit(self,rid,stage,executor):
  with self.lock:
   parent=self.parent(rid,executor)
   selected=next((s for s in parent['plan']['stages']if s['id']==stage),None)
   if selected is None:raise ValueError('WORKFLOW_STAGE_UNKNOWN')
   receipts=[]
   for dependency in selected['dependsOn']:
    row=self._row(rid,dependency)
    if row is None or not row['receipt_json']:raise ValueError('WORKFLOW_DEPENDENCY_NOT_PUBLISHED')
    self._bound(row,parent)
   # Preserve original FormalAdapters context: all verified prior receipts carry
   # creation incarnation pins, not only the immediate dependency's publication.
   for prior in parent['plan']['stages']:
    saved=self._row(rid,prior['id'])
    if saved is not None and saved['receipt_json']:
     self._bound(saved,parent);receipts.append(json.loads(saved['receipt_json']))
   row=self._row(rid,stage)
   retry=parent.get('approvedRetries',{}).get(stage)
   key=retry['childKey'] if retry else rid+'.'+stage+'.1'
   if row is not None and row['child_key']!=key:
    self._bound(row,parent)
    if not retry or type(retry.get('attempt'))is not int or retry['attempt']<2 or key!=rid+'.'+stage+'.'+str(retry['attempt']) or row['receipt_json']:
     raise ValueError('WORKFLOW_APPROVED_RETRY_REQUIRED')
    previous=row['child_key'].removeprefix(rid+'.'+stage+'.')
    if not previous.isdecimal() or int(previous)+1!=retry['attempt']:raise ValueError('WORKFLOW_RETRY_SEQUENCE_CHANGED')
    with self.common.tx():
     self.common.db.execute('INSERT INTO resident_workflow_child_history VALUES(?,?,?,?,?)',(rid,stage,row['child_key'],retry['retryRequestId'],json.dumps(dict(row),sort_keys=True)))
     self.common.db.execute('UPDATE resident_workflow_children SET child_key=?,child_id=NULL,channel=NULL,receipt_json=NULL WHERE parent_id=? AND stage_id=?',(key,rid,stage))
    row=self._row(rid,stage)
   if row is None:
    with self.common.tx():
     self.common.db.execute('INSERT INTO resident_workflow_children(parent_id,stage_id,parent_hash,plan_hash,child_key) VALUES(?,?,?,?,?)',(rid,stage,parent['requestHash'],parent['planHash'],key))
   else:
    self._bound(row,parent)
    if row['child_key']!=key:raise ValueError('WORKFLOW_CHILD_KEY_CHANGED')
    if row['child_id']:return {'requestId':row['child_id'],'channel':row['channel']}
   # FormalAdapters durably freezes the command before original intake. Lost
   # responses replay this same key; no fresh generation attempt is fabricated.
   self.register_child(parent,stage,key,None)
   child=self.adapter.accept(selected,parent['request'],key,parent['actor'],receipts)
   if not isinstance(child,dict)or not child.get('requestId')or not child.get('channel'):
    raise ValueError('WORKFLOW_CHILD_ACCEPT_UNCONFIRMED')
   current=self.parent(rid,executor)
   if current['requestHash']!=parent['requestHash']or current['planHash']!=parent['planHash']:
    raise ValueError('WORKFLOW_PARENT_CHANGED_DURING_SUBMIT')
   self.register_child(parent,stage,key,child)
   with self.common.tx():
    self.common.db.execute('UPDATE resident_workflow_children SET child_id=?,channel=? WHERE parent_id=? AND stage_id=?',(child['requestId'],child['channel'],rid,stage))
   return child
 def view(self,rid,stage,executor):
  with self.lock:
   parent=self.parent(rid,executor);row=self._row(rid,stage)
   if row is None or not row['child_id']:raise ValueError('WORKFLOW_CHILD_NOT_ACCEPTED')
   self._bound(row,parent)
   if row['channel']=='animation-native' and stage.startswith('animation_'):
    if getattr(self.adapter,'enabled',False)is not True or not callable(getattr(self.adapter,'advance',None)):raise ValueError('WORKFLOW_NATIVE_CONTROLLER_REQUIRED')
    value=self.adapter.advance(stage,row['child_id'],parent['actor'])
   else:value=self.adapter.view(stage,row['child_id'],parent['actor'])
   if value.get('requestId')!=row['child_id']:raise ValueError('WORKFLOW_CHILD_ID_CHANGED')
   if value.get('state')=='published':
    receipt=value.get('receipt',{})
    if receipt.get('verified')is not True or receipt.get('requestId')!=row['child_id']or receipt.get('stageId')!=stage:
     raise ValueError('WORKFLOW_PUBLICATION_REQUIRED')
    encoded=json.dumps(receipt,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
    if row['receipt_json']and row['receipt_json']!=encoded:raise ValueError('WORKFLOW_PUBLICATION_CHANGED')
    with self.common.tx():
     self.common.db.execute('UPDATE resident_workflow_children SET receipt_json=? WHERE parent_id=? AND stage_id=?',(encoded,rid,stage))
   self.observe_child(parent,stage,value)
   return value
