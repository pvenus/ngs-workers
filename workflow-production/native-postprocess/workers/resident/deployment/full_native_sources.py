"""Original, pinned request/root/session readers for five-role native composition.

Only parent operation IDs are accepted. No generation creation, new pin, request
registration or globally installed factory. All DB access is method-time only.
"""
import json
from pathlib import Path
from .resident_native_host import NativeOperationBinding
from .request_selector import digest
from .domain_resolvers import FRAMES,ANIMATION

class NativeSourceReaders:
 def __init__(self,sources,common,*,service_roots,member_owned,standalone_frame):
  self.sources,self.common=sources,common
  self.roots=dict(service_roots);self.member=member_owned;self.standalone=standalone_frame
  if not callable(member_owned)or not callable(standalone_frame):raise ValueError('NATIVE_SOURCE_HANDOFF_REQUIRED')
 def host_readers(self):
  from local.services.npc_workflow_proofs import HostReaders
  return HostReaders(self.common,self.roots)
 def resolve(self,rid):
  row=self.common.row(rid)
  owned=self.member(row)is True
  kind=row['request_type']
  if kind==FRAMES[0]:
   if not owned and self.standalone(row)is not True:raise ValueError('NATIVE_SOURCE_NOT_OWNED')
   operation='frames'
  else:
   if not owned:raise ValueError('NATIVE_SOURCE_NOT_OWNED')
   if kind in ('npc.pose.expand-eight-directions','npc.pose.regenerate-one'):operation='pose'
   elif kind in (ANIMATION[0],'npc.animation.generate-eight-directions'):operation='animation'
   else:
    saved=self.common.db.execute('SELECT * FROM npc_single_batches WHERE id=?',(rid,)).fetchone()
    if saved is None:raise ValueError('NATIVE_SOURCE_PARENT_REQUIRED')
    request=json.loads(saved['request_json']);selection=json.loads(saved['selection_json']);stages=selection.get('stages',[])
    if saved['actor']!=row['requester'] or digest(request)!=saved['input_hash'] or selection.get('requestHash')!=saved['input_hash']:
     raise ValueError('NATIVE_SOURCE_PARENT_CHANGED')
    if len(stages)!=1 or stages[0].get('candidateCount')!=1:raise ValueError('NATIVE_SOURCE_SINGLE_REQUIRED')
    stage=stages[0].get('id')
    if stage=='portrait':operation='portrait'
    elif stage in ('overview','art','category-add','category-modify'):operation='planning'
    else:raise ValueError('NATIVE_SOURCE_METADATA_LEGACY_ONLY')
  root,npc=self.host_readers().context(rid)
  return NativeOperationBinding(operation,str(root))
 def original_guard(self,rid):
  self.resolve(rid)
  return self.host_readers().admission(rid)
 def session(self,rid):
  self.resolve(rid)
  root,npc=self.host_readers().context(rid)
  folder=root/'ownership';path=folder/(digest([npc,rid])+'.json')
  if folder.is_symlink()or not folder.is_dir()or path.is_symlink()or not path.is_file():raise ValueError('NATIVE_SOURCE_ORIGINAL_PIN_REQUIRED')
  value=json.loads(path.read_text())
  if value.get('npcId')!=npc or value.get('requestId')!=rid or not value.get('generationId') or value.get('released'):
   raise ValueError('NATIVE_SOURCE_ORIGINAL_PIN_CHANGED')
  # Original Session does not write unless its explicit lifecycle methods run.
  return self.sources.session_type(folder,npc,rid)

 def released_session(self,rid):
  """Read-only retry evidence. Never constructs an executable Session."""
  from types import SimpleNamespace
  binding=self.resolve(rid)
  if binding.operation not in ('pose','animation'):raise ValueError('NATIVE_RELEASED_MEDIA_REQUIRED')
  root,npc=self.host_readers().context(rid)
  folder=root/'ownership';path=folder/(digest([npc,rid])+'.json')
  if folder.is_symlink()or not folder.is_dir()or path.is_symlink()or not path.is_file():raise ValueError('NATIVE_SOURCE_ORIGINAL_PIN_REQUIRED')
  value=json.loads(path.read_text());guard=value.get('guard')
  if (value.get('npcId')!=npc or value.get('requestId')!=rid or not value.get('generationId')
      or value.get('released')is not True or not isinstance(guard,dict)
      or any(guard.get(k)!=value.get(k)for k in ('npcId','generationId','requestId'))
      or not isinstance(guard.get('leaseToken'),str)or not guard['leaseToken']):
   raise ValueError('NATIVE_SOURCE_RELEASED_PIN_CHANGED')
  return SimpleNamespace(path=path)
