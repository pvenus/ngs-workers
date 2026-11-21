"""Actual original NGS document-only domain composition, explicitly opt-in.

No implicit media routes or credentials. The source manifest MUST include the
reviewed external_children change; original Session and CAS finalizer remain.
"""
import json
from ..adapters.frame_workspace import digest
from .document_parent import build_parent_bridge
from .domain_resolvers import DomainResolvers,DOCUMENT,validate_document_envelope
from ..adapters.external_parent_runner import ExternalParentRunner
from ..adapters.document_pds import DocumentDraftWorkspace

class LazyCategoryExecutor:
 def __init__(self,sources,common):self.sources,self.common=sources,common
 def __getattr__(self,name):
  # Constructor runs only under a Request scope after preflight, never at import.
  executor=self.sources.modules['category_executor'].CategorySingleExecutor(self.common)
  return getattr(executor,name)

def document_domain_factory(sources,*,scratch_root,preflight,admitted_parent,ownership_lifecycle=None):
 if not callable(preflight)or not callable(admitted_parent):raise ValueError('DOCUMENT_HANDOFF_PREFLIGHT_REQUIRED')
 def build(common,bridge,model,terminal):
  executor=LazyCategoryExecutor(sources,common)
  def service_for_parent(rid):
   row=common.db.execute('SELECT selection_json FROM npc_single_batches WHERE id=?',(rid,)).fetchone()
   if row is None:raise ValueError('DOCUMENT_PARENT_NOT_FOUND')
   stages=json.loads(row[0]).get('stages',[])
   if len(stages)!=1 or stages[0].get('id')not in ('overview','art','category-add','category-modify'):raise ValueError('DOCUMENT_PARENT_ROUTE_UNSUPPORTED')
   # Candidate single_host forwards mode to SingleBatch. An unpatched release
   # rejects the keyword rather than silently executing the legacy inline worker.
   return sources.modules['parent_host'].build(common,stages[0]['id'],external_children=True)
  def wrap_guard(original):
   if ownership_lifecycle is None:return original
   from .document_image_native_proof import native_child_guard
   return native_child_guard(original,ownership_lifecycle,scratch_root=scratch_root,kind='planning-attempt')
  parent,guard=build_parent_bridge(common=common,request_authority=bridge,executor=executor,
   parent_service=service_for_parent,session_type=sources.session_type,
   ownership_module=sources.modules['ownership'],category_dispatch=sources.category_dispatch,guard_wrapper=wrap_guard)
  from ..adapters.document_context_authority import authorize as authorize_context
  parent.context_authorize=lambda c:authorize_context(parent,ownership_lifecycle,bridge,c)
  def owns(row):
   if admitted_parent(row)is not True:return False
   selection=json.loads(row['selection_json']);stages=selection.get('stages',[])
   return (len(stages)==1 and stages[0].get('id')in ('overview','art','category-add','category-modify')
    and stages[0].get('candidateCount')==1 and len(selection.get('invocations',[]))==1
    and selection.get('registry',{}).get('selectionPolicy',{}).get('executionContract')=='worker-count.v1')
  def ready(row):
   if not row.get('parent_id'):return False
   native=common.db.execute('SELECT * FROM npc_single_batches WHERE id=?',(row['parent_id'],)).fetchone()
   if native is None or not owns(dict(native)):return False
   p=common.row(row['parent_id'])
   return p['requester']==row['requester'] and p['state']in ('queued','retry_wait')
  resolvers=DomainResolvers(common,None,document_bridge=parent)
  selector=resolvers.selector({DOCUMENT:validate_document_envelope},ready,(DOCUMENT,))
  def cleanup(claim):
   # Supervisor already requires actual stopped evidence. Workspace verifies exact
   # claim.json ownership and removes only this attempt; no PDS source writes.
   return DocumentDraftWorkspace(scratch_root,None,None).cleanup(claim,None)
  def initialize():
   # Original tables MUST already exist from formal intake. No legacy data bootstrap.
   tables={r[0] for r in common.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
   if not {'npc_single_batches','npc_single_invocations','npc_single_duplicate_retries'}<=tables:raise ValueError('DOCUMENT_ORIGINAL_INTAKE_SCHEMA_REQUIRED')
  def failure_guard_reader(rid):
   service=service_for_parent(rid)
   row=common.db.execute('SELECT request_json FROM npc_single_batches WHERE id=?',(rid,)).fetchone()
   request=json.loads(row[0]);npc=request.get('npcId')or request.get('characterRef',{}).get('npcId')
   root=service.root/'ownership';path=root/(digest([npc,rid])+'.json')
   if root.is_symlink()or path.is_symlink()or not path.is_file():raise ValueError('FAILURE_ORIGINAL_PIN_REQUIRED')
   value=json.loads(path.read_text());guard=value.get('guard')
   if value.get('npcId')!=npc or value.get('requestId')!=rid or not isinstance(guard,dict):raise ValueError('FAILURE_ORIGINAL_GUARD_CHANGED')
   if any(guard.get(k)!=value.get(k)for k in ('npcId','requestId','generationId')):raise ValueError('FAILURE_ORIGINAL_GUARD_CHANGED')
   return guard
  def failure_finalize(service,rid,actor):
   import importlib
   authority=getattr(bridge,'failure_fence',None)
   if authority is None:raise ValueError('DOCUMENT_FAILURE_FENCE_REQUIRED')
   module=importlib.import_module('local.services.npc_failure_release')
   return module.finish_failed(service,rid,actor,failure_reader=authority.seal_for_failure)
  return {'selector':selector,'verify_publication':parent.verify_publication,'guard_resolver':guard,
   'inspect_publication':parent.inspect_recovery,'verify_recovery_publication':parent.verify_recovery_publication,
   'failure_guard_reader':failure_guard_reader,'cleanup':cleanup,'preflight':preflight,'initialize_schema':initialize,'document_bridge':parent,
   'parent_loop':ExternalParentRunner(common,service_for_parent,owns_parent=owns,failure_finalizer=failure_finalize)}
 return build
