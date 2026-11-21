"""Formal HQ music domain composition; no implicit legacy image completion.

Image drafts are NOT original portrait results: pixel-pair and parent application
must be connected before enabling IMAGE. Refuse that route rather than marking a
parent complete using an unrelated immutable artifact receipt.
"""
from pathlib import Path
from .domain_resolvers import DomainResolvers,MUSIC
from .request_selector import digest
from ..adapters.music_intake import MusicIntake
from ..adapters.music_pds import MusicWorkspace
from ..adapters.image_pds import MediaArtifactClient,media_publication_verifier
from workers.audio.hq.hq_contract import validate_payload


def image_music_domain_factory(*,pds_url,pds_token,music_scratch_root,
        admitted_request,preflight,cleanup,enable_image=False,client_factory=None):
 if enable_image:raise ValueError('IMAGE_ORIGINAL_PARENT_PAIR_FINALIZER_NOT_CONNECTED')
 if not all(callable(v)for v in (admitted_request,preflight,cleanup)):raise ValueError('MEDIA_TRUSTED_BINDINGS_REQUIRED')
 root=Path(music_scratch_root)
 if not root.is_absolute()or root.is_symlink()or not root.is_dir()or root.resolve()!=root:raise ValueError('MEDIA_SCRATCH_ROOT_INVALID')
 def build(common,bridge,model,terminal):
  intake=MusicIntake(common)
  client=(client_factory or MediaArtifactClient)(pds_url,pds_token)
  verify=media_publication_verifier(client)
  def original(claim):
   if claim.capability!='music.generate':raise ValueError('MEDIA_DOMAIN_CAPABILITY_REQUIRED')
   value=intake.payload(claim.request_id)
   if value['parameters']!=dict(claim.parameters)or value['expectedRevision']!=claim.expected_revision or tuple(value['inputRefs'])!=tuple(claim.input_refs):raise ValueError('MUSIC_ORIGINAL_PAYLOAD_CHANGED')
   return value
  def source(claim,folder=None):
   # Request-service scope only. HTTP adapter must authenticate _lease first.
   value=original(claim);binding=value['binding']
   return {'revision':binding['expectedRevision'],'projectId':binding['projectId'],
           'targetId':binding['targetId'],'execution':{},'inputFiles':[]}
  space=MusicWorkspace(root,client,source,cleanup)
  def ready(row):return admitted_request(row)is True
  selector=DomainResolvers(common,None,music_intake=intake).selector({MUSIC:validate_payload},ready,(MUSIC,))
  def checked(claim,receipt):
   value=original(claim);proof=verify(claim,receipt);raw=proof['reservedReceipt']
   expected={**value['binding'],'capability':claim.capability,'requestHash':digest(dict(claim.parameters))}
   if any(raw.get(k)!=v for k,v in expected.items()) or raw.get('artifact',{}).get('mimeType')!='audio/wav':
    raise ValueError('MUSIC_PUBLICATION_SCOPE_CHANGED')
   return proof
  def inspect(claim):original(claim);return space.recover(claim)
  def resume(claim,publication_hash):original(claim);return space.resume_publication(claim,publication_hash)
  def clean(claim):original(claim);return space.cleanup(claim,None)
  return {'capabilities':('music.generate',),'selector':selector,'verify_publication':checked,
          'inspect_publication':inspect,'verify_recovery_publication':checked,'cleanup':clean,
          'resume_publication':resume,'initialize_schema':intake.initialize_schema,
          'preflight':preflight,'music_intake':intake,'media_source':source,
          'publicationScope':'immutable-filex-artifact','canonicalCatalogRegistration':False}
 return build


class LazyPortraitExecutor:
 def __init__(self,common,root):self.common,self.root=common,Path(root)
 def __getattr__(self,name):
  from local.services.npc_workflow.single_portrait import SinglePortraitExecutor
  return getattr(SinglePortraitExecutor(self.common,self.root),name)


def portrait_pair_domain_factory(*, executor=None, executor_root=None, pds_url, pds_token, scratch_root,
        journal_root, ownership_root, admitted_request, preflight, cleanup, ownership_lifecycle=None,pds_pair_reader=None,direct_pds=False):
 """Canonical single-parent portrait route. No model calls or new admission.

 Executor is the pinned original SinglePortraitExecutor. Existing parent session
 and invocation registration are mandatory; unrelated legacy workflows fail closed.
 Worker source reads remain PDS-only, through the exported trusted source port.
 """
 import json
 from contextlib import contextmanager
 from .animation_frame_site import existing_root
 from .domain_resolvers import IMAGE,IMAGE_SINGLE
 from .document_image_native_proof import canonical_child_result
 from .portrait_pair_service import PortraitPairService
 from ..adapters.ownership_guard import PinnedOwnershipGuardResolver
 from ..adapters.portrait_asset_client import PortraitAssetClient
 from ..adapters.pds_animation import AnimationPublicationClient
 from ..adapters.image_pds import PortraitSource,PortraitContextReader
 from local.services.npc_workflow import portrait_pairs
 from local.services.npc_workflow.single_portrait import require_parent
 from local.services.npc_workflow.portrait_pose_stage import finalize_pair
 from local.services.portrait.png_alpha import validate_stage_output
 from local.adapters.portrait.adapters import LocalHttpNpcPort
 from local.services import npc_ownership
 scratch,journal,ownership=map(existing_root,(scratch_root,journal_root,ownership_root))
 if (executor is None)==(executor_root is None):raise ValueError('PAIR_EXECUTOR_BINDING_REQUIRED')
 record_root=existing_root(executor_root) if executor_root is not None else None
 if not all(callable(v) for v in (admitted_request,preflight,cleanup)):
  raise ValueError('PAIR_TRUSTED_DOMAIN_PORTS_REQUIRED')
 def build(common,bridge,model,terminal):
  bound=executor if executor is not None else LazyPortraitExecutor(common,record_root)
  if bound.common is not common:raise ValueError('PAIR_EXECUTOR_COMMON_CHANGED')
  def original(rid):
   row=common.row(rid);path=bound._record(rid)
   if path.is_symlink() or not path.is_file():raise ValueError('PAIR_ORIGINAL_RECORD_MISSING')
   value=json.loads(path.read_text())
   if row['request_type']!=IMAGE[0] or value.get('schemaVersion')!=IMAGE[1] or value.get('parentId')!=row['parent_id'] or value.get('actor')!=row['requester']:
    raise ValueError('PAIR_CHILD_AUTHORITY_CHANGED')
   parent=common.row(row['parent_id'])
   if parent['requester']!=row['requester'] or parent['project_id']!=row['project_id'] or parent['request_type']!='npc.workflow':raise ValueError('PAIR_PARENT_OWNER_CHANGED')
   require_parent(common,row['parent_id'],row['requester'],value['request'],value['stage'],value['childKey'])
   registered=common.db.execute('SELECT child_id FROM npc_single_invocations WHERE parent_id=? AND child_id=?',(row['parent_id'],rid)).fetchall()
   if len(registered)!=1:raise ValueError('PAIR_PARENT_CHILD_NOT_REGISTERED')
   if value.get('regeneratePixelOnly') or value.get('reprocessedPixelSource') or value.get('posePreset') not in ('representative','idle','combat'):
    raise ValueError('PAIR_FRESH_PAIR_ONLY')
   return value
  def session(claim):
   value=original(claim.request_id)
   if dict(claim.parameters)!=value:raise ValueError('PAIR_CLAIM_PAYLOAD_CHANGED')
   npc=value['request']['characterRef']['npcId'];parent=value['parentId']
   path=ownership/(digest([npc,parent])+'.json')
   if path.is_symlink() or not path.is_file():raise ValueError('PAIR_ORIGINAL_PARENT_PIN_REQUIRED')
   return npc_ownership.Session(ownership,npc,parent,call=lambda *a,**k:(_ for _ in ()).throw(ValueError('PAIR_NEW_ADMISSION_FORBIDDEN')))
  guard=PinnedOwnershipGuardResolver(session)
  if ownership_lifecycle is not None:
   from .document_image_native_proof import native_child_guard
   guard=native_child_guard(guard,ownership_lifecycle,scratch_root=scratch,kind="portrait-stage")
  @contextmanager
  def scope(claim):
   token=npc_ownership._CURRENT.set(guard(claim)['guard'])
   try:
    with npc_ownership.stage(claim.request_id):yield
   finally:npc_ownership._CURRENT.reset(token)
  def assets(claim):return PortraitAssetClient(pds_url,pds_token,lambda:guard(claim)['guard'],claim.request_id)
  if type(direct_pds)is not bool:raise ValueError('PAIR_DIRECT_MODE_INVALID')
  reader=pds_pair_reader
  if reader is None and direct_pds:
   from ..adapters.portrait_pair_pds import DirectPortraitPairClient
   reader=DirectPortraitPairClient(pds_url,ownership_guard=lambda c:guard(c)['guard']).read
  service_type=PortraitPairService
  extra={}
  if reader is not None:
   from .portrait_pair_service import DirectPortraitPairService
   if not callable(reader):raise ValueError('PAIR_DIRECT_READER_REQUIRED')
   service_type=DirectPortraitPairService;extra={'pds_pair_reader':reader,'parent_guard':lambda c:guard(c)['guard']}
  service=service_type(bridge,bound,scratch_root=scratch,journal_root=journal,
      pairs=portrait_pairs,validate_alpha=validate_stage_output,finalize_pair=finalize_pair,
      assets=assets,selection=lambda c:LocalHttpNpcPort(pds_url),ownership_scope=scope,
      receipt_reader=lambda c,r:assets(c).publication_receipt(r),**extra)
  original_record=service.record
  def bound_record(claim):
   original(claim.request_id)
   return original_record(claim)
  service.record=bound_record
  def source(claim,folder):
   original(claim.request_id)
   if Path(folder)!=scratch/digest([claim.request_id,claim.attempt_id]):raise ValueError('PAIR_SOURCE_WORKSPACE_CHANGED')
   client=AnimationPublicationClient(pds_url,lambda:guard(claim)['guard'],claim.request_id)
   return PortraitSource(PortraitContextReader(client),client.asset)(claim,Path(folder))
  def checked(claim,receipt):original(claim.request_id);return service.verify_publication(claim,receipt)
  def clean(claim):
   original(claim.request_id)
   return cleanup(claim,scratch/digest([claim.request_id,claim.attempt_id])) is True
  selector=DomainResolvers(common,None,portrait_record=original).selector({IMAGE:lambda v:True,IMAGE_SINGLE:lambda v:True},lambda r:admitted_request(r)is True,(IMAGE,IMAGE_SINGLE))
  return {'capabilities':('image.generate',),'selector':selector,'portrait_pair_service':service,'original_record_reader':original,
      'canonical_child_result':lambda rid:canonical_child_result(common,service,rid),
      'guard_resolver':guard,'media_source':source,'verify_publication':checked,
      'verify_recovery_publication':checked,'inspect_publication':service.inspect_publication,
      'resume_publication':service.resume_publication,'cleanup':clean,'preflight':preflight,
      'initialize_schema':lambda:None,
      'publicationScope':'registered-npc-portrait-pair','parentOwnershipReleased':False}
 return build


def combined_image_domain_factory(pair_factory,pose_factory):
 """One IMAGE capability, exact immutable DTO routing, no fallback worker."""
 from .pose_direction_site import SCHEMA as POSE_SCHEMA
 schemas={'npc-workflow-portrait-single.v1':0,POSE_SCHEMA:1}
 def build(common,bridge,model,terminal):
  domains=[f(common,bridge,model,terminal) for f in (pair_factory,pose_factory)]
  if any(d['capabilities']!=('image.generate',)for d in domains):raise ValueError('IMAGE_DOMAIN_CAPABILITY_CHANGED')
  def owner(c):
   schema=c.parameters.get('schemaVersion')
   if c.capability!='image.generate' or schema not in schemas:raise ValueError('IMAGE_DOMAIN_ROUTE_UNSUPPORTED')
   return domains[schemas[schema]]
  def selector(profile):
   candidates=[v for d in domains if (v:=d['selector'](profile)) is not None]
   return min(candidates,key=lambda v:(common.row(v['requestId'])['created_at'],v['requestId']))if candidates else None
  result={'capabilities':('image.generate',),'selector':selector,
      'portrait_pair_service':domains[0]['portrait_pair_service'],'pose_direction_service':domains[1]['pose_direction_service'],
      'initialize_schema':lambda:[d['initialize_schema']()for d in domains],
      'preflight':lambda:all(d['preflight']()is True for d in domains)}
  for key in ('original_record_reader','canonical_child_result'):
   if key in domains[0]:result[key]=domains[0][key]
  if 'verify_child_publication'in domains[1]:result['verify_pose_child_publication']=domains[1]['verify_child_publication']
  for key in ('verify_publication','verify_recovery_publication','guard_resolver','media_source','inspect_publication','resume_publication','cleanup'):
   def routed(c,*args,_key=key,**kwargs):return owner(c)[_key](c,*args,**kwargs)
   result[key]=routed
  return result
 return build
