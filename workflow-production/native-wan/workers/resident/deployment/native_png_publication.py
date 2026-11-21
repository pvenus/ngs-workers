"""Request-side metadata authorization for the existing PDS storage principal.

Never accepts file bytes, executes storage, or exports Filex transport credentials.
The enclosing existing authenticated service dispatch must enforce its principal;
this callable is not a public/worker route.
"""
import re
from dataclasses import asdict
from .native_png_storage import NativePNGStorage
SHA=re.compile('[a-f0-9]{64}')
PNG=re.compile('[a-f0-9]{64}\\.png')
class NativePNGPublicationAuthority:
 def __init__(self,storage):
  if not isinstance(storage,NativePNGStorage):raise ValueError('NATIVE_PUBLICATION_AUTHORITY_REQUIRED')
  self.storage=storage
 def authorize(self,claim,action,metadata):
  if type(metadata)is not dict:raise ValueError('NATIVE_PUBLICATION_METADATA')
  guard,path=self.storage._scope(claim)
  if action=='asset':
   if set(metadata)!={'assetId','sha256','bytes'}or not isinstance(metadata['assetId'],str)or not PNG.fullmatch(metadata['assetId'])or metadata['sha256']+'.png'!=metadata['assetId']or type(metadata['bytes'])is not int or metadata['bytes']<=0:raise ValueError('NATIVE_PUBLICATION_METADATA')
  elif action=='commit':
   if set(metadata)!={'path','inputHash','manifestHash','assetIds'}or metadata['path']!=path or metadata['inputHash']!=claim.expected_revision or not isinstance(metadata['manifestHash'],str)or not SHA.fullmatch(metadata['manifestHash'])or type(metadata['assetIds'])is not list or not metadata['assetIds']or any(type(a)is not str or not PNG.fullmatch(a)for a in metadata['assetIds']):raise ValueError('NATIVE_PUBLICATION_METADATA')
  elif action=='receipt':
   if metadata:raise ValueError('NATIVE_PUBLICATION_METADATA')
  else:raise ValueError('NATIVE_PUBLICATION_ACTION')
  # Scope resolver performed its original pre/post live binding checks. Nothing
  # here retires or extends a lease; Filex still validates original guard at write.
  return {'guard':guard,'stageRequestId':claim.request_id,'inputHash':claim.expected_revision,'allowedPath':path,'action':action,'metadata':metadata}
