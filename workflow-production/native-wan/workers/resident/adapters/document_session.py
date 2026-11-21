"""Original parent Session resolver. Request service only; never repins/admit."""
from pathlib import Path
from .frame_workspace import digest

class ParentSessionResolver:
 def __init__(self,parent_bridge,parent_service,session_type):
  self.bridge,self.parent_service,self.session_type=parent_bridge,parent_service,session_type
 def __call__(self,claim):
  record,binding=self.bridge.record(claim.request_id)
  if binding!=claim.parameters:raise ValueError('DOCUMENT_ENVELOPE_CHANGED')
  parent=self.parent_service(record['parentId'])
  root=Path(parent.root)/'ownership';npc=record['request']['npcId'];rid=record['parentId']
  path=root/(digest([npc,rid])+'.json')
  # Check before Session constructor, which can create its parent directory.
  if root.is_symlink()or not root.is_dir()or path.is_symlink()or not path.is_file():raise ValueError('NPC_OWNERSHIP_INTAKE_PIN_REQUIRED')
  session=self.session_type(root,npc,rid)
  if session.path!=path:raise ValueError('NPC_OWNERSHIP_SESSION_PATH_CHANGED')
  return session

def ownership_scope(guard_resolver,ownership_module):
 """Carry an already admitted guard through the original PDS client context.
 No Session.enter/pin, no generation allocation and no new authorization.
 """
 from contextlib import contextmanager
 @contextmanager
 def scope(claim):
  value=guard_resolver(claim)
  if value.get('stageRequestId')!=claim.request_id or not isinstance(value.get('guard'),dict):raise ValueError('DOCUMENT_OWNERSHIP_SCOPE_INVALID')
  token=ownership_module._CURRENT.set(value['guard'])
  try:
   with ownership_module.stage(claim.request_id):yield
  finally:ownership_module._CURRENT.reset(token)
 return scope
