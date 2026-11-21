"""Read-only original frame owner/attempt and exact PDS absence evidence."""
import hashlib,inspect,json,urllib.request,urllib.error,urllib.parse
from dataclasses import asdict
from pathlib import Path
from ..adapters.attempt_directory_lock import directory_lock
from ..adapters.frame_full_workspace import FullFrameWorkspace
from ..adapters.pds_category_files import header
from ..adapters.failure_fence import digest
from .resident_frame_proof import checked

def reader(*,pds_url,original,session_for_claim,workspace):
 parsed=urllib.parse.urlsplit(pds_url)
 if parsed.scheme!='http' or parsed.hostname!='127.0.0.1' or parsed.username or parsed.password or parsed.path not in ('','/') or parsed.query or parsed.fragment or not parsed.port:raise ValueError('FRAME_NO_POST_PDS_ORIGIN')
 from ..adapters import frame_full_workspace,frame_workspace,pds_frames,pds_category_files
 modules=(frame_full_workspace,frame_workspace,pds_frames,pds_category_files)
 def sources():return {module.__name__:hashlib.sha256(Path(inspect.getfile(module)).read_bytes()).hexdigest()for module in modules}
 def read(claim):
  def need(ok):
   if not ok:raise ValueError('FRAME_NO_POST_PROVENANCE')
  need(claim.capability=='frames.extract'and claim.parameters.get('scalePreset','full')=='full')
  before_sources=sources()
  original(claim.request_id,claim.capability,claim.parameters)
  session=session_for_claim(claim,readonly=True);state=json.loads(session.path.read_text());guard=state.get('guard')
  need(isinstance(guard,dict)and set(guard)=={'npcId','generationId','requestId','leaseToken'}and guard.get('requestId')==claim.request_id and guard.get('npcId')==claim.parameters['npcId']and not state.get('released')and all(guard[k]==state.get(k)for k in ('npcId','generationId','requestId')))
  space=workspace(claim);need(type(space)is FullFrameWorkspace);folder=checked(space._folder(claim))
  with directory_lock(folder)as exists:
   need(exists)
   def raw(name):
    p=checked(folder/name);need(p.is_file());return p.read_bytes()
   marker=raw('claim.json');manifest=raw('frame-manifest.json');need(json.loads(marker)==json.loads(json.dumps(asdict(claim))))
   m=json.loads(manifest);need(m.get('requestId')==claim.request_id and isinstance(m.get('artifacts'),dict)and bool(m['artifacts']))
   for name in ('publication-intent.json','publication-receipt.json'):
    path=folder/name;need(not path.exists()and not path.is_symlink())
   for sha,name in m['artifacts'].items():
    p=checked(name);need(folder in p.parents and p.is_file()and hashlib.sha256(p.read_bytes()).hexdigest()==sha)
   # Exact route/status/body. A proxy/missing-route 404 is not authoritative.
   from ..adapters.pds_category_files import NoRedirect
   request=urllib.request.Request(pds_url.rstrip('/')+'/v1/npcs/animation-frames/requests/'+claim.request_id,headers={'X-NPC-Ownership-Guard':header(guard),'X-NPC-Stage-Request-Id':claim.request_id})
   try:
    with urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect).open(request,timeout=30)as response:response.read(1)
   except urllib.error.HTTPError as exc:
    need(exc.code==404 and json.loads(exc.read(65537))=={'error':'animation_frames_request_not_found'})
   else:raise ValueError('FRAME_NO_POST_RECEIPT_EXISTS')
   need(json.loads(session.path.read_text())==state and raw('claim.json')==marker and raw('frame-manifest.json')==manifest)
   for name in ('publication-intent.json','publication-receipt.json'):need(not (folder/name).exists()and not (folder/name).is_symlink())
   source=sources();need(source==before_sources)
   return {'requestId':claim.request_id,'claimHash':digest(asdict(claim)),'workspaceHash':digest({'path':str(folder),'marker':hashlib.sha256(marker).hexdigest()}),'manifestHash':hashlib.sha256(manifest).hexdigest(),'sourceHash':digest(source),'guardHash':digest(guard),'receiptState':'authoritative-not-found','intentAbsent':True}
 return read
