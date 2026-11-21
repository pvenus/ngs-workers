"""Native readback through existing same-Mac PDS routes and original guard.

No Filex paths, credentials, synthetic receipts or new server route.
"""
from urllib.parse import urlencode
from ..adapters.pds_animation import AnimationPublicationClient
from ..adapters.frame_workspace import digest

PREFIX='canonical-files/binari/npcs/'
class NativePNGReadback(AnimationPublicationClient):
 def generation(self,npc):
  g=self._guard()
  if npc!=g['npcId']:raise ValueError('NATIVE_READ_SCOPE')
  return self._frame_call('/v1/npcs/ownership/generation?'+urlencode({'npcId':npc}))
 def publications(self):
  g=self._guard()
  q={k:g[k] for k in ('npcId','generationId')};q['parentRequestId']=g['requestId']
  return self._frame_call('/v1/npcs/ownership/publications?'+urlencode(q))
 def document(self,mid,*,expected_request_id=None):
  g=self._guard();prefix=PREFIX+g['npcId']+'/'
  if not isinstance(mid,str) or not mid.startswith(prefix) or any(x in ('','..','.') for x in mid.split('/')):
   raise ValueError('NATIVE_READ_SCOPE')
  relative=mid[len(prefix):]
  if relative.split('/')[0] not in ('animation-sequences','animation-alpha','animation-processed'):
   raise ValueError('NATIVE_READ_SCOPE')
  snapshot=self._frame_call('/v1/npcs/files')
  documents=snapshot['documents']
  if type(documents)is not dict:raise ValueError('NATIVE_READ_DOCUMENTS')
  rows=self.publications()['receipts']
  key=mid[len(PREFIX):]
  if key not in documents:
   if any(mid in r.get('hashes',{}) or expected_request_id is not None and r.get('stageRequestId')==expected_request_id for r in rows):raise ValueError('NATIVE_READ_RECEIPT_DOCUMENT_MISSING')
   if self._guard()!=g:raise ValueError('NATIVE_READ_GUARD_CHANGED')
   return None
  m=documents[key]
  rid=expected_request_id if expected_request_id is not None else m.get('requestId')
  if not isinstance(rid,str) or not rid or ('requestId'in m and m['requestId']!=rid):raise ValueError('NATIVE_READ_REQUEST_CHANGED')
  matches=[r for r in rows if r.get('stageRequestId')==rid]
  if len(matches)!=1:raise ValueError('NATIVE_READ_RECEIPT_REQUIRED')
  r=matches[0]
  if (r.get('npcId')!=g['npcId'] or r.get('generationId')!=g['generationId'] or r.get('parentRequestId')!=g['requestId'] or r.get('verified')is not True or r.get('status')!='committed' or r.get('hashes')!={mid:digest(m)}):
   raise ValueError('NATIVE_READ_RECEIPT_SCOPE')
  if self._guard()!=g:raise ValueError('NATIVE_READ_GUARD_CHANGED')
  return dict(manifest=m,receipt={k:v for k,v in r.items()if k not in ('npcId','verified')})
 def readback(self,phase,task):
  kind={'raw':'animation-sequences','alpha':'animation-alpha','pixel':'animation-processed'}[phase]
  mid=PREFIX+task['npcId']+'/'+kind+'/'+task['posePreset']+'/'+task['presetId']
  if phase!='pixel':mid+='/'+task['direction']
  mid+='.json';value=self.document(mid,expected_request_id=task['requestId'])
  if value is None:return None
  if phase!='pixel' and value['manifest']['requestId']!=task['requestId']:raise ValueError('NATIVE_READ_REQUEST_CHANGED')
  return dict(manifestId=mid,**value)
