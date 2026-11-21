"""Read-only original document scope. Never emits a guard or enters native lifecycle."""
from dataclasses import asdict
from .frame_workspace import digest
from .ownership_guard import PinnedOwnershipGuardResolver

def authorize(parent,lifecycle,bridge,claim):
 if lifecycle is None:raise ValueError('DOCUMENT_CONTEXT_NATIVE_REQUIRED')
 def original(c):
  record,binding=parent.record(c.request_id)
  if binding!=c.parameters:raise ValueError('DOCUMENT_ENVELOPE_CHANGED')
  rid=record['parentId'];session=lifecycle.session(rid)
  value=PinnedOwnershipGuardResolver(lambda _:session)(c)
  lifecycle.scope(value['guard'])
  if value['scopeRequestId']!=rid:raise ValueError('DOCUMENT_CONTEXT_PARENT_CHANGED')
  return value
 g=bridge._resolve_ownership_guard(claim,original)
 return {'schemaVersion':'document-context-authorization.v1','claimHash':digest(asdict(claim)),
  'requestId':claim.request_id,'npcId':g['npcId'],'expectedRevision':claim.expected_revision,'readOnly':True}
