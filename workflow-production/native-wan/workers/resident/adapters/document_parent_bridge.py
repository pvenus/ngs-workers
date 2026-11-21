"""Request-service bridge to existing CategoryService and parent draft contract.

Host supplies the existing reviewed CategorySingleExecutor; workers never import
NGS, open its DB, or call this class directly. No canonical apply is performed.
"""
import json
from dataclasses import asdict
import uuid
from pathlib import Path
from .frame_workspace import digest,save

KEYS=('variation','stage','request','childKey','actor','parentId')

class ParentDraftBridge:
 def __init__(self,common,executor,authorize_claim,dispatch_factory,ownership_scope,publication_started=None):
  self.common,self.executor,self.authorize_claim=common,executor,authorize_claim
  if not callable(dispatch_factory):raise ValueError('DOCUMENT_CATEGORY_DISPATCH_REQUIRED')
  self.publication_started=publication_started
  self.dispatch_factory=dispatch_factory
  if not callable(ownership_scope):raise ValueError('DOCUMENT_OWNERSHIP_SCOPE_REQUIRED')
  self.ownership_scope=ownership_scope
 def record(self,rid):
  row=dict(self.common.row(rid));path=self.executor._record(rid)
  if path.is_symlink() or not path.is_file():raise ValueError('DOCUMENT_PARENT_RECORD_REQUIRED')
  record=json.loads(path.read_text());binding={k:record[k]for k in KEYS}
  refs=self.common.db.execute('SELECT domain,schema_version,input_hash FROM common_payload_refs WHERE request_id=?',(rid,)).fetchall()
  expected=('npc.category-candidate','ngs.npc-category-candidate.v1',digest(binding))
  if row['request_type']!='npc.workflow' or not any(tuple(r)==expected for r in refs) or row['requester']!=record['actor'] or row['parent_id']!=record['parentId']:raise ValueError('DOCUMENT_PARENT_BINDING_INVALID')
  parent=self.common.db.execute('SELECT actor,request_json,input_hash FROM npc_single_batches WHERE id=?',(record['parentId'],)).fetchone()
  if parent is None or parent['actor']!=record['actor'] or json.loads(parent['request_json'])!=record['request'] or parent['input_hash']!=digest(record['request']):raise ValueError('DOCUMENT_PARENT_REQUEST_CHANGED')
  if record['stage'].get('id')not in ('overview','art','category-add','category-modify') or record['stage'].get('candidateCount')!=1:raise ValueError('DOCUMENT_PARENT_STAGE_INVALID')
  return record,binding
 def payload(self,row,ref):
  record,binding=self.record(row['request_id']);r=record['request']
  return {'requestId':row['request_id'],'parameters':binding,'inputRefs':[ref['reference_id']],
          'expectedRevision':str(r['baseRevision']),
          'binding':{'projectId':row['project_id'],'targetId':r['npcId'],'expectedRevision':str(r['baseRevision'])}}
 def authorize(self,claim):
  authorized=self.authorize_claim(claim)
  if authorized is False:raise ValueError('DOCUMENT_CLAIM_NOT_AUTHORIZED')
  record,binding=self.record(claim.request_id)
  if binding!=claim.parameters:raise ValueError('DOCUMENT_ENVELOPE_CHANGED')
  pipeline,category=self.executor._category(None)
  try:state=category.get(record['localRequestId'])
  finally:category.close();pipeline.close()
  cid=record['request'].get('categoryId') if record['request']['mode']=='modify' else 'category.'+str(uuid.uuid5(uuid.NAMESPACE_URL,'npc-category:'+state['requestHash']))
  return {'requestId':claim.request_id,'parentId':record['parentId'],'localRequestId':record['localRequestId'],
          'categoryId':cid,'categoryRequestHash':state['requestHash'],
          'publicationScope':'request-draft-candidate','parentOwnsApply':True}
 def _journal(self,claim):return self.executor.root/(claim.request_id+'.resident-draft.json')
 def submit(self,claim,packet):
  authority=self.authorize(claim)
  if packet.get('parent')!=authority or packet.get('requestHash')!=digest(claim.parameters) or packet.get('sourceApplied')is not False or any(packet.get(k)!=v for k,v in {'requestId':claim.request_id,'attemptId':claim.attempt_id,'fencingToken':claim.fencing_token,'publicationScope':'request-draft-candidate'}.items()):raise ValueError('DOCUMENT_DRAFT_PACKET_INVALID')
  journal=self._journal(claim)
  if journal.exists():
   if json.loads(journal.read_text())['packetHash']!=digest(packet):raise ValueError('DOCUMENT_DRAFT_REPLAY_CONFLICT')
   return
  output=packet['output'];record,_=self.record(claim.request_id)
  if not isinstance(output,dict)or set(output)not in ({'proposals'},{'proposals','fileContent'})or not isinstance(output.get('proposals'),list)or len(output['proposals'])!=1 or 'fileContent'in output and not isinstance(output['fileContent'],str):raise ValueError('DOCUMENT_DRAFT_OUTPUT_INVALID')
  class SavedGenerator:
   model_calls=0
   last_file_content=output.get('fileContent')
   def __call__(self,context):
    # Existing CategoryService rechecks original revision/incarnation/PDS target.
    if any(context['request'].get(k)!=record['request'].get(k)for k in ('npcId','baseRevision','mode','categoryId','intent')):raise ValueError('DOCUMENT_CATEGORY_CONTEXT_CHANGED')
    return output['proposals']
  pipeline,category=self.executor._category(SavedGenerator())
  try:
   state=category.get(record['localRequestId'])
   if state.get('proposals'):
    if state['proposals']['proposals']!=output['proposals']:raise ValueError('DOCUMENT_EXISTING_DRAFT_CONFLICT')
   if not state.get('proposals') or pipeline.get(record['localRequestId']).get('state')!='awaiting_selection':
    with self.ownership_scope(claim):
     if self.publication_started is not None:self.publication_started(record['parentId'])
     result=self.dispatch_factory(pipeline,category).once('binari',worker_id='npc-single-planning',lease_seconds=120,local_id=record['localRequestId'])
    if result.get('state')!='awaiting_selection':raise ValueError('DOCUMENT_CATEGORY_DISPATCH_PENDING')
    state=category.get(record['localRequestId'])
   if state['state']!='awaiting_selection' or state.get('applied')or state.get('selection'):raise ValueError('DOCUMENT_DRAFT_STATE_INVALID')
   proposal=state['proposals']['proposals'][0]
   receipt={'requestId':claim.request_id,'localRequestId':record['localRequestId'],'state':'completed','candidateCount':1,
            'proposal':proposal,'proposalHash':state['proposalHash'],'requestHash':state['requestHash'],
            'baseRevision':state['inputs']['baseRevision'],'publicationScope':'request-draft-candidate',
            'representativeChanged':False,'sourceApplied':False,'verified':True,'variation':record.get('variation'),
            'seedSupported':False,'comparisonHash':digest({'categoryName':' '.join(proposal['categoryName'].split()),'content':' '.join(proposal['content'].split())})}
   legacy=self.executor.root/(claim.request_id+'.receipt.json')
   if legacy.exists():
    if json.loads(legacy.read_text())!=receipt:raise ValueError('DOCUMENT_PARENT_RECEIPT_CONFLICT')
   else:save(legacy,receipt)
   save(journal,{'requestId':claim.request_id,'attemptId':claim.attempt_id,'fencingToken':claim.fencing_token,
                'verified':True,'packetHash':digest(packet),'publicationScope':'request-draft-candidate','sourceApplied':False,
                'draftRef':'npc.category-candidate:'+claim.request_id,'revision':state['proposalHash'],'parentReceipt':receipt})
  finally:category.close();pipeline.close()
 def read(self,claim):
  self.authorize(claim);p=self._journal(claim)
  if p.is_symlink()or not p.is_file():raise ValueError('DOCUMENT_DRAFT_RECEIPT_MISSING')
  value=json.loads(p.read_text())
  if (value['requestId'],value['attemptId'],value['fencingToken'])!=(claim.request_id,claim.attempt_id,claim.fencing_token):raise ValueError('DOCUMENT_DRAFT_RECEIPT_BINDING')
  return value
 def verify_publication(self,claim,receipt):
  self.authorize(claim)
  return self.verify_recovery_publication(claim,receipt)

 def verify_recovery_publication(self,claim,receipt):
  """Internal Request recovery only; never exposed through worker HTTP."""
  value=self.read_service_receipt(claim)
  if receipt.asset_refs!=(value['draftRef'],)or receipt.revision!=value['revision']:raise ValueError('DOCUMENT_PARENT_RECEIPT_INVALID')
  return {'sourceReceipt':value['parentReceipt'],'publication':value['parentReceipt']}

 def read_service_receipt(self,claim):
  """Immutable evidence only; Request reconciler may use an expired source claim.
  No worker endpoint exposes this method. No lease is renewed or reconstructed.
  """
  grant=self.common.db.execute('SELECT claim_json FROM resident_resource_grants WHERE grant_id=?',(claim.resource_grant,)).fetchone()
  if grant is None or json.loads(grant['claim_json'])!=json.loads(json.dumps(asdict(claim))):raise ValueError('DOCUMENT_RECOVERY_CLAIM_CHANGED')
  record,binding=self.record(claim.request_id)
  if binding!=claim.parameters:raise ValueError('DOCUMENT_RECOVERY_INPUT_CHANGED')
  path=self._journal(claim)
  if path.is_symlink()or not path.is_file():raise ValueError('DOCUMENT_DRAFT_RECEIPT_MISSING')
  value=json.loads(path.read_text())
  if (value.get('requestId'),value.get('attemptId'),value.get('fencingToken'))!=(claim.request_id,claim.attempt_id,claim.fencing_token):raise ValueError('DOCUMENT_DRAFT_RECEIPT_BINDING')
  parent_path=self.executor.root/(claim.request_id+'.receipt.json')
  if parent_path.is_symlink()or not parent_path.is_file()or json.loads(parent_path.read_text())!=value.get('parentReceipt'):raise ValueError('DOCUMENT_RECOVERY_PARENT_RECEIPT_CHANGED')
  return value
 def inspect_recovery(self,claim):
  from ..contracts import Receipt
  path=self._journal(claim)
  if not path.exists():return None
  value=self.read_service_receipt(claim)
  return Receipt(claim.request_id,claim.attempt_id,claim.fencing_token,(value['draftRef'],),value['revision'],True)
