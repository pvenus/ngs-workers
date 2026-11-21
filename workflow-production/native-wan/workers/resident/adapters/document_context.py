"""Token-free document context authorization; no write or ownership capability."""
from dataclasses import asdict
from urllib.parse import urlsplit,parse_qs
from .frame_workspace import digest
from ..backends.document_npc import request

def authorize(claim,drafts,path,packet):
 r=request(claim)
 if packet is None:
  u=urlsplit(path);q=parse_qs(u.query,keep_blank_values=True)
  if u.path!='/api/worker-context/catalog' or q!={'projectId':['binari'],'npcId':[r['npcId']],'workerId':['npc-planning']}:raise ValueError('DOCUMENT_CONTEXT_TARGET_INVALID')
 else:
  categories=['common.npc-planning']+(['unique.npc-category:'+r['categoryId']] if r['mode']=='modify' else [])
  if path!='/api/worker-context/read' or set(packet)!={'projectId','npcId','npcRevision','sourceVersion','categories'} or packet['projectId']!='binari' or packet['npcId']!=r['npcId'] or packet['npcRevision']!=r['baseRevision'] or packet['categories']!=categories or not isinstance(packet['sourceVersion'],str) or not packet['sourceVersion']:raise ValueError('DOCUMENT_CONTEXT_TARGET_INVALID')
 expected={'schemaVersion':'document-context-authorization.v1','claimHash':digest(asdict(claim)),
  'requestId':claim.request_id,'npcId':r['npcId'],'expectedRevision':claim.expected_revision,'readOnly':True}
 try:actual=drafts.context_authorize(claim)
 except TimeoutError:raise TimeoutError('DOCUMENT_CONTEXT_AUTHORIZATION_TIMEOUT') from None
 if actual!=expected:raise ValueError('DOCUMENT_CONTEXT_AUTHORIZATION_CHANGED')
