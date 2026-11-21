"""Request-service portrait pair validation, with no generation or direct Filex IO.

The source modules are pinned deployment objects. Validation recomputes the fixed
pixel transform and preserves original prompt/context, allowing only workspace
reference relocation. A validated bundle is not a publication/completion receipt.
"""
import copy,hashlib,json
from dataclasses import asdict
from pathlib import Path
from ..backends.image_music_workspace import safe_file
from .request_selector import digest

FILES=('output.png','provider-proof.json','pixel-claim.json','pixel-source.png','pixel-output.png','pixel-result.json')

def sha(raw):return hashlib.sha256(raw).hexdigest()

def _proof(value,claim,raw):
 if (not isinstance(value,dict) or value.get('actualToolOutput') is not True
     or value.get('sha256')!=sha(raw) or any(value.get(k)!=claim.get(k) for k in ('index','providerClaim','requestHash','providerRequestHash'))):
  raise ValueError('PAIR_NATIVE_PROOF_CHANGED')

def _manifest(value,folder,npc_id,reference_reader=None):
 if not isinstance(value,dict) or set(value)!={'references','manifestHash'} or value['manifestHash']!=digest({'references':value['references']}):raise ValueError('PAIR_REFERENCE_MANIFEST_CHANGED')
 if not isinstance(value['references'],list):raise ValueError('PAIR_REFERENCE_LIST_REQUIRED')
 for ref in value['references']:
  if ref.get('npcId')!=npc_id or ref.get('mainPortraitAssetId')!=ref.get('sha256','')+'.png':raise ValueError('PAIR_REFERENCE_TARGET_CHANGED')
  if reference_reader is not None:
   if sha(reference_reader(ref))!=ref['sha256']:raise ValueError('PAIR_REFERENCE_BYTES_CHANGED')
   continue
  path=Path(ref['localPath'])
  try:relative=path.relative_to(folder)
  except ValueError:raise ValueError('PAIR_REFERENCE_OUTSIDE_WORKSPACE')from None
  if sha(safe_file(folder,str(relative)).read_bytes())!=ref['sha256']:raise ValueError('PAIR_REFERENCE_BYTES_CHANGED')
 return value

def _canonical_claim(value):
 """Erase only permitted reference relocation, never prompt or other metadata."""
 result=copy.deepcopy(value);manifest=result.get('referenceManifest')
 if manifest is not None:
  for ref in manifest['references']:ref['localPath']='<workspace>'
  manifest['manifestHash']=digest({'references':manifest['references']})
  result['providerRequest']['arguments']['referenced_image_paths']=['<workspace>']*len(manifest['references'])
  result['providerRequest']['metadata']['referenceManifest']=copy.deepcopy(manifest)
 result['providerRequestHash']=digest(result['providerRequest'])
 return result

def validate_pair_bundle(claim,folder,record,*,pairs,validate_alpha,reference_reader=None):
 folder=Path(folder)
 if folder.is_symlink() or folder.resolve()!=folder:raise ValueError('PAIR_WORKSPACE_REQUIRED')
 if json.loads(safe_file(folder,'claim.json').read_text())!=json.loads(json.dumps(asdict(claim))):raise ValueError('PAIR_ATTEMPT_CHANGED')
 if dict(claim.parameters)!=record or record.get('schemaVersion')!='npc-workflow-portrait-single.v1' or record.get('posePreset') not in ('representative','idle','combat'):
  raise ValueError('PAIR_ORIGINAL_RECORD_REQUIRED')
 if record.get('regeneratePixelOnly') or record.get('reprocessedPixelSource'):raise ValueError('PAIR_REUSE_NOT_CONNECTED')
 meta=json.loads(safe_file(folder,'artifacts/pair-manifest.json').read_text())
 expected={'schemaVersion':'resident.portrait-pair.v1','requestId':claim.request_id,'attemptId':claim.attempt_id,'fencingToken':claim.fencing_token,'originalRecordHash':digest(record)}
 if set(meta)!=set(expected)|{'effectivePortraitClaim','files'} or any(meta.get(k)!=v for k,v in expected.items()) or set(meta['files'])!=set(FILES):raise ValueError('PAIR_MANIFEST_SCOPE_CHANGED')
 data={name:safe_file(folder,'artifacts/'+name).read_bytes() for name in FILES}
 for name,raw in data.items():
  if meta['files'][name]!={'sha256':sha(raw),'bytes':len(raw)}:raise ValueError('PAIR_FILE_HASH_CHANGED')
 native=meta['effectivePortraitClaim'];original=record['claim']
 if original.get('providerRequestHash')!=digest(original['providerRequest']) or native.get('providerRequestHash')!=digest(native['providerRequest']) or _canonical_claim(native)!=_canonical_claim(original):raise ValueError('PAIR_ORIGINAL_PROMPT_CHANGED')
 npc=record.get('generationRequest',record['request'])['characterRef']['npcId']
 if 'referenceManifest'in native:
  manifest=_manifest(native['referenceManifest'],folder,npc,reference_reader)
  if native['providerRequest'].get('metadata',{}).get('referenceManifest')!=manifest or native['providerRequest']['arguments'].get('referenced_image_paths')!=[r['localPath'] for r in manifest['references']]:raise ValueError('PAIR_NATIVE_REFERENCE_CHANGED')
 elif native['providerRequest']['arguments'].get('referenced_image_paths'):raise ValueError('PAIR_NATIVE_REFERENCE_CHANGED')
 proof=json.loads(data['provider-proof.json']);_proof(proof,native,data['output.png'])
 validate_alpha(data['output.png'],native['providerRequest']['outputContract'],'portrait')
 pixel=json.loads(data['pixel-claim.json']);manifest=_manifest(pixel['referenceManifest'],folder,npc,reference_reader)
 refs=manifest['references'];portrait_sha=sha(data['output.png'])
 if len(refs)!=1 or refs[0]['sha256']!=portrait_sha or refs[0].get('portraitEntryHash')!=digest({'portraitSha256':portrait_sha,'posePreset':record['posePreset']}):raise ValueError('PAIR_PIXEL_REFERENCE_CHANGED')
 descriptor=pairs.descriptor(pairs.pixel_prompt(record),manifest)
 expected_pixel={'index':0,'providerClaim':'claim.'+digest([claim.request_id,'pixel'])[:48],'providerRequest':descriptor,'providerRequestHash':digest(descriptor),'requestHash':digest(record),'referenceManifest':manifest}
 if pixel!=expected_pixel:raise ValueError('PAIR_PIXEL_CLAIM_CHANGED')
 result=json.loads(data['pixel-result.json']);_proof(result['providerProof'],pixel,data['pixel-source.png'])
 validate_alpha(data['pixel-source.png'],descriptor['outputContract'],'pixel-native')
 normalized,postprocess=pairs.postprocess_pixel(data['pixel-source.png'])
 pipeline={'portraitSha256':portrait_sha,'referencePortraitSha256':portrait_sha,'pixelSourceSha256':sha(data['pixel-source.png']),'postprocessInputSha256':sha(data['pixel-source.png']),'pixelSha256':sha(normalized),'postprocessCompleted':True}
 if normalized!=data['pixel-output.png'] or result.get('pixelPostprocess')!=postprocess or result.get('pixelPipelineProof')!=pipeline or result.get('referencePortraitSha256')!=portrait_sha or result.get('promptHash')!=digest(descriptor['arguments']['prompt']):raise ValueError('PAIR_PIXEL_PIPELINE_CHANGED')
 # staged asset identifiers are deliberately not returned as canonical assets.
 return {'portrait':data['output.png'],'portraitProof':proof,'pixel':normalized,'pixelSource':data['pixel-source.png'],'pixelProof':result['providerProof'],'pixelPostprocess':postprocess,'pixelPipelineProof':pipeline,'pixelPromptHash':result['promptHash'],'manifestHash':digest(meta)}

class PortraitPairService:
 """Service-only whole-pair publication; no intermediate draft can complete.

 assets(claim) is a PDS-only canonical registrar, never CanonicalFileAdapter.
 selection(claim) is the original guarded PDS scoped-CAS port. Both are operator
 configured. Original finalizer owns plan/CAS/readback semantics unchanged.
 """
 def __init__(self,authority,executor,*,scratch_root,journal_root,pairs,validate_alpha,finalize_pair,assets,selection,ownership_scope,receipt_reader):
  self.authority,self.executor=authority,executor
  self.scratch,self.journal=Path(scratch_root),Path(journal_root)
  for root in (self.scratch,self.journal):
   if not root.is_absolute() or root.is_symlink() or root.resolve()!=root or not root.is_dir():raise ValueError('PAIR_SERVICE_ROOT_REQUIRED')
  if not all(callable(v) for v in (validate_alpha,finalize_pair,assets,selection,ownership_scope,receipt_reader)):raise ValueError('PAIR_PDS_PORTS_REQUIRED')
  self.pairs,self.validate_alpha,self.finalize=pairs,validate_alpha,finalize_pair
  self.assets,self.selection,self.scope=assets,selection,ownership_scope
  self.receipt_reader=receipt_reader
 def record(self,claim):
  row=self.authority.common.row(claim.request_id)
  if claim.capability!='image.generate' or row['request_type']!='npc.workflow.stage' or not row['parent_id']:raise ValueError('PAIR_FORMAL_CHILD_REQUIRED')
  path=self.executor._record(claim.request_id)
  if path.is_symlink() or not path.is_file():raise ValueError('PAIR_ORIGINAL_RECORD_MISSING')
  record=json.loads(path.read_text())
  refs=self.authority.common.db.execute('SELECT * FROM common_payload_refs WHERE request_id=?',(claim.request_id,)).fetchall()
  matches=[r for r in refs if r['domain'] in ('npc.workflow.portrait','npc.single.portrait') and r['schema_version']=='npc-workflow-portrait-single.v1' and r['reference_id']==claim.request_id and r['input_hash']==digest(record)]
  if len(matches)!=1 or dict(claim.parameters)!=record or record.get('actor')!=row['requester'] or record.get('parentId')!=row['parent_id'] or not isinstance(record.get('generationRequest',record.get('request')),dict):raise ValueError('PAIR_ORIGINAL_RECORD_CHANGED')
  return record
 def folder(self,claim):return self.scratch/digest([claim.request_id,claim.attempt_id])
 def path(self,claim):return self.journal/(digest([claim.request_id,claim.attempt_id])+'.json')
 def authorize(self,claim):
  record=self.record(claim);self.authority.authorize_publication(claim)
  return {'requestId':claim.request_id,'publicationScope':'registered-npc-portrait-pair','requiredSourceApplied':True,'originalRecordHash':digest(record)}
 def _immutable(self,path,value):
  from ..adapters.frame_workspace import save
  if path.exists() or path.is_symlink():
   if path.is_symlink() or json.loads(path.read_text())!=value:raise ValueError('PAIR_DURABLE_RECEIPT_CONFLICT')
  else:save(path,value)
 def _packet(self,claim,packet):
  if not isinstance(packet,dict) or set(packet)!={'manifest'}:raise ValueError('PAIR_PACKET_INVALID')
  root=self.folder(claim)
  if json.loads(safe_file(root,'artifacts/pair-manifest.json').read_text())!=packet['manifest']:raise ValueError('PAIR_PACKET_MANIFEST_CHANGED')
  return root
 def submit(self,claim,packet):
  self.authorize(claim)
  return self._locked_publish(claim,packet)
 def _locked_publish(self,claim,packet):
  import os,fcntl
  path=self.journal/(digest([claim.request_id,claim.attempt_id])+'.lock')
  fd=os.open(path,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
  try:
   fcntl.flock(fd,fcntl.LOCK_EX)
   return self._publish(claim,packet)
  finally:os.close(fd)
 def _publish(self,claim,packet):
  from types import SimpleNamespace
  record=self.record(claim);root=self._packet(claim,packet)
  if self.path(claim).exists():
   value=self.read(claim)
   if value['packetHash']!=digest(packet):raise ValueError('PAIR_PUBLICATION_REPLAY_CHANGED')
   self._mirror(claim,value['parentReceipt'])
   self.authority.finish_publication(claim,digest(packet),value['reservedReceipt'])
   return value
  value=validate_pair_bundle(claim,root,record,pairs=self.pairs,validate_alpha=self.validate_alpha)
  reservation=self.authority.prepare_publication(claim,digest(packet))
  output={'sha256':sha(value['portrait']),'size':len(value['portrait'])}
  provenance={'runId':claim.request_id,'registrationIntentId':claim.request_id,'compositionHash':digest(record),'promptHash':record['promptHash'],'sourceRefsHash':digest(record['context']),'providerProofHash':digest(value['portraitProof']),'output':output}
  with self.scope(claim):
   assets=self.assets(claim);port=self.selection(claim)
   asset=assets.register(value['portrait'],'binari',claim.request_id,provenance);assets.verify(asset,output)
   pixel_output={'sha256':sha(value['pixel']),'size':len(value['pixel'])}
   pixel_provenance={'runId':claim.request_id+'.pixel','registrationIntentId':claim.request_id,'compositionHash':digest(record),'promptHash':value['pixelPromptHash'],'sourceRefsHash':digest(record['context']),'providerProofHash':digest(value['pixelProof']),'postprocess':value['pixelPostprocess'],'pixelSourceSha256':sha(value['pixelSource']),'output':pixel_output}
   pixel=assets.register(value['pixel'],'binari',claim.request_id+'.pixel',pixel_provenance);assets.verify(pixel,pixel_output)
   for a,o in ((asset,output),(pixel,pixel_output)):
    if any(a.get(k)!=v for k,v in {'projectId':'ProjectBS','kind':'portrait','mimeType':'image/png','status':'available',**o}.items()) or not a.get('id') or a.get('publicationScope')=='unpublished-worker-staging':raise ValueError('PAIR_CANONICAL_ASSET_REQUIRED')
   draft={'schemaVersion':'npc-workflow-portrait-draft.v1','requestId':claim.request_id,'state':'completed','published':True,'publicationScope':'request-draft-file-asset','candidateCount':1,'outputMode':'request-draft.single.v1','sourceApplied':False,'representativeChanged':False,'asset':asset,'promptHash':record['promptHash'],'sourceTrace':record['context'],'providerProof':value['portraitProof'],'posePreset':record['posePreset'],'pixelAsset':pixel,'pixelProviderProof':value['pixelProof'],'referencePortraitAssetId':record.get('referencePortraitAssetId'),'pixelReferencePortraitSha256':sha(value['portrait']),'pairReady':True,'pixelPipelineProof':value['pixelPipelineProof'],'pixelPostprocess':value['pixelPostprocess']}
   result=self.finalize(SimpleNamespace(selection_port=port),{**record,'generationRequest':record.get('generationRequest',record['request'])},draft,root/'artifacts',value['portrait'])
   # Original scoped publication is idempotent and returns its durable Filex
   # receipt. Re-read the exact operation rather than asserting mirror validity.
   state=json.loads(safe_file(root,'artifacts/pair-publication.json').read_text())
   saved=port.publish_portrait(state['plan'],claim.request_id,record.get('generationRequest',record['request'])['characterRef']['npcId'])
   canonical=saved.get('publicationReceipt')
   if not isinstance(canonical,dict) or canonical.get('status')!='committed' or canonical.get('inputHash')!=saved.get('inputHash') or canonical.get('resultMetadata',{}).get('scopedPortrait',{}).get('requestId')!=claim.request_id:raise ValueError('PAIR_CANONICAL_RECEIPT_REQUIRED')
  if result.get('sourceApplied') is not True or result.get('publicationScope')!='registered-npc-portrait-pair' or result.get('publication',{}).get('verified') is not True or result.get('publication',{}).get('authority')!='filex' or result.get('portraitAssetId')!=sha(value['portrait'])+'.png' or result.get('pixelAssetId')!=sha(value['pixel'])+'.png':raise ValueError('PAIR_CANONICAL_PUBLICATION_UNVERIFIED')
  reserved={k:reservation[k] for k in ('requestId','attemptId','fencingToken','capability','projectId','targetId','expectedRevision','requestHash')}
  reserved.update(receiptKind='npc-portrait-pair-service.v1',publicationHash=digest(packet),authority='filex',status='committed',verified=True,sourceReceipt=result,canonicalReceipt=canonical)
  reserved['receiptHash']=digest(reserved)
  receipt={'requestId':claim.request_id,'attemptId':claim.attempt_id,'fencingToken':claim.fencing_token,'packetHash':digest(packet),'verified':True,'sourceApplied':True,'publicationScope':'registered-npc-portrait-pair','revision':str(result['revision']),'assetRefs':[result['portraitAssetId'],result['pixelAssetId']],'parentReceipt':result,'reservedReceipt':reserved}
  self._immutable(self.path(claim),receipt)
  self._mirror(claim,result)
  self.authority.finish_publication(claim,digest(packet),reserved)
  return receipt
 def _mirror(self,claim,result):
  mirror=self.executor.root/(claim.request_id+'.portrait')
  if mirror.is_symlink():raise ValueError('PAIR_PARENT_MIRROR_PATH')
  mirror.mkdir(exist_ok=True)
  self._immutable(mirror/'result.json',result)
 def read(self,claim):
  self.record(claim);p=self.path(claim)
  if p.is_symlink() or not p.is_file():raise ValueError('PAIR_PUBLICATION_NOT_READY')
  value=json.loads(p.read_text())
  if any(value.get(k)!=v for k,v in {'requestId':claim.request_id,'attemptId':claim.attempt_id,'fencingToken':claim.fencing_token}.items()) or value.get('verified') is not True or value.get('sourceApplied') is not True:raise ValueError('PAIR_SAVED_RECEIPT_SCOPE')
  reserved=value.get('reservedReceipt',{});canonical=reserved.get('canonicalReceipt')
  if not isinstance(canonical,dict) or reserved.get('receiptHash')!=digest({k:v for k,v in reserved.items() if k!='receiptHash'}) or self.receipt_reader(claim,canonical['requestId'])!=canonical:raise ValueError('PAIR_CANONICAL_READBACK_CHANGED')
  parent=value.get('parentReceipt',{});metadata=canonical.get('resultMetadata',{});binding=metadata.get('scopedPortrait',{})
  if (reserved.get('sourceReceipt')!=parent or reserved.get('publicationHash')!=value['packetHash']
      or binding.get('requestId')!=claim.request_id or str(metadata.get('revision'))!=value.get('revision')
      or parent.get('revision')!=metadata.get('revision') or parent.get('publication',{}).get('documentHash')!=metadata.get('documentHash')
      or value.get('assetRefs')!=[parent.get('portraitAssetId'),parent.get('pixelAssetId')]):raise ValueError('PAIR_CANONICAL_RESULT_CHANGED')
  return value
 def verify_publication(self,claim,receipt):
  value=self.read(claim);receipt.validate(claim)
  if receipt.asset_refs!=tuple(value['assetRefs']) or receipt.revision!=value['revision']:raise ValueError('PAIR_COMPLETION_RECEIPT_CHANGED')
  return {'sourceReceipt':value['parentReceipt'],'publication':{'requestId':claim.request_id,'verified':True,'scope':'registered-npc-portrait-pair','receipt':value['parentReceipt']['publication']},'reservedReceipt':value['reservedReceipt']}
 def inspect_publication(self,claim):
  from ..contracts import Receipt
  if not self.path(claim).exists():return None
  value=self.read(claim)
  return Receipt(claim.request_id,claim.attempt_id,claim.fencing_token,tuple(value['assetRefs']),value['revision'],True)
 def resume_publication(self,claim,expected_hash):
  packet={'manifest':json.loads(safe_file(self.folder(claim),'artifacts/pair-manifest.json').read_text())}
  if digest(packet)!=expected_hash:raise ValueError('PAIR_RECOVERY_HASH_CHANGED')
  if self.authority.verify_stopped(claim) is not True:raise ValueError('PAIR_RECOVERY_EXECUTION_NOT_STOPPED')
  with self.authority.transaction():
   self.authority._lease(claim)
   row=self.authority.common.db.execute('SELECT publication_hash FROM resident_publications WHERE grant_id=?',(claim.resource_grant,)).fetchone()
   if row is None or row['publication_hash']!=expected_hash:raise ValueError('PAIR_RECOVERY_RESERVATION_REQUIRED')
  self._locked_publish(claim,packet)
  return self.inspect_publication(claim)

class DirectPortraitPairService(PortraitPairService):
 """Opt-in metadata authority. PDS owns all byte validation and canonical writes.

 The two injected readers are trusted server ports, never worker claims. The
 inherited canonical receipt reader independently verifies the Filex commit.
 """
 def __init__(self,*args,pds_pair_reader,parent_guard,**kwargs):
  super().__init__(*args,**kwargs)
  if not callable(pds_pair_reader) or not callable(parent_guard):raise ValueError('PAIR_DIRECT_PORTS_REQUIRED')
  self.pds_pair_reader,self.parent_guard=pds_pair_reader,parent_guard
 def _metadata(self,claim,packet):
  if not isinstance(packet,dict) or set(packet)!={'manifest'}:raise ValueError('PAIR_PACKET_INVALID')
  m=packet['manifest'];expected={'schemaVersion':'resident.portrait-pair.v1','requestId':claim.request_id,'attemptId':claim.attempt_id,'fencingToken':claim.fencing_token,'originalRecordHash':digest(dict(claim.parameters))}
  if not isinstance(m,dict) or set(m)!=set(expected)|{'effectivePortraitClaim','files'} or any(m.get(k)!=v for k,v in expected.items()) or not isinstance(m['files'],dict) or set(m['files'])!=set(FILES):raise ValueError('PAIR_MANIFEST_SCOPE_CHANGED')
  import re
  for value in m['files'].values():
   if not isinstance(value,dict) or set(value)!={'sha256','bytes'} or type(value['bytes'])is not int or not 0<value['bytes']<=32*1024*1024 or not re.fullmatch('[a-f0-9]{64}',value.get('sha256','')):raise ValueError('PAIR_FILE_METADATA_INVALID')
  return packet
 def intent_path(self,claim):return self.journal/(digest([claim.request_id,claim.attempt_id])+'.metadata.json')
 def prepare(self,claim,packet):
  self._metadata(claim,packet);record=self.record(claim)
  guard=self.parent_guard(claim)
  if not isinstance(guard,dict) or set(guard)!={'npcId','generationId','requestId','leaseToken'} or guard['requestId']!=record['parentId'] or guard['npcId']!=record.get('generationRequest',record['request'])['characterRef']['npcId']:raise ValueError('PAIR_DIRECT_GUARD_CHANGED')
  with self.authority.transaction():
   self.authority.authorize_publication(claim)
   reservation=self.authority.prepare_publication(claim,digest(packet))
  self._immutable(self.intent_path(claim),packet)
  return {'reservation':reservation,'record':record,'parentGuard':guard}
 def submit(self,claim,packet):raise ValueError('PAIR_DIRECT_PDS_UPLOAD_REQUIRED')
 def finish(self,claim,packet):
  self._metadata(claim,packet);self.record(claim)
  reservation=self.read_reservation(claim,digest(packet))['reservation']
  p=self.intent_path(claim)
  if p.is_symlink() or not p.is_file() or json.loads(p.read_text())!=packet:raise ValueError('PAIR_DIRECT_INTENT_REQUIRED')
  value=self.pds_pair_reader(claim,digest(packet))
  expected={'requestId':claim.request_id,'attemptId':claim.attempt_id,'fencingToken':claim.fencing_token,'packetHash':digest(packet)}
  if not isinstance(value,dict) or set(value)!=set(expected)|{'parentReceipt','canonicalReceipt'} or any(value.get(k)!=v for k,v in expected.items()):raise ValueError('PAIR_DIRECT_READBACK_SCOPE')
  parent=value['parentReceipt'];canonical=value['canonicalReceipt'];meta=canonical.get('resultMetadata',{});binding=meta.get('scopedPortrait',{})
  if canonical.get('status')!='committed' or binding.get('requestId')!=claim.request_id or self.receipt_reader(claim,canonical.get('requestId'))!=canonical:raise ValueError('PAIR_CANONICAL_READBACK_CHANGED')
  manifest=packet['manifest']['files']
  if (parent.get('requestId')!=claim.request_id or parent.get('state')!='completed' or parent.get('candidateCount')!=1 or parent.get('pairReady')is not True or parent.get('sourceApplied')is not True or parent.get('publicationScope')!='registered-npc-portrait-pair' or parent.get('publication',{}).get('verified')is not True or parent.get('publication',{}).get('authority')!='filex' or parent.get('revision')!=meta.get('revision') or parent.get('publication',{}).get('documentHash')!=meta.get('documentHash') or parent.get('portraitAssetId')!=manifest['output.png']['sha256']+'.png' or parent.get('pixelAssetId')!=manifest['pixel-output.png']['sha256']+'.png'):raise ValueError('PAIR_CANONICAL_RESULT_CHANGED')
  reserved={k:reservation[k] for k in ('requestId','attemptId','fencingToken','capability','projectId','targetId','expectedRevision','requestHash')}
  reserved.update(receiptKind='npc-portrait-pair-service.v1',publicationHash=digest(packet),authority='filex',status='committed',verified=True,sourceReceipt=parent,canonicalReceipt=canonical);reserved['receiptHash']=digest(reserved)
  receipt={**expected,'verified':True,'sourceApplied':True,'publicationScope':'registered-npc-portrait-pair','revision':str(parent['revision']),'assetRefs':[parent['portraitAssetId'],parent['pixelAssetId']],'parentReceipt':parent,'reservedReceipt':reserved}
  self._mirror(claim,parent)
  self._immutable(self.path(claim),receipt)
  self.authority.finish_publication(claim,digest(packet),reserved)
  return {'accepted':True}
 def resume_publication(self,claim,expected_hash):
  p=self.intent_path(claim)
  if p.is_symlink() or not p.is_file():raise ValueError('PAIR_DIRECT_INTENT_REQUIRED')
  packet=json.loads(p.read_text())
  if digest(packet)!=expected_hash:raise ValueError('PAIR_RECOVERY_HASH_CHANGED')
  # No upload/regeneration here; pending/expired authority remains fail-closed.
  self.finish(claim,packet)
  return self.inspect_publication(claim)

 def read_reservation(self,claim,packet_hash):
  # Trusted PDS callback: reading cannot create or refresh a reservation.
  record=self.record(claim)
  with self.authority.transaction():
   # Upload authorization is live, unlike an already durable receipt lookup.
   # An existing prepared reservation must not revive an expired attempt.
   self.authority.authorize_publication(claim)
   row=self.authority.common.db.execute('SELECT publication_hash FROM resident_publications WHERE grant_id=?',(claim.resource_grant,)).fetchone()
   if row is None or row[0]!=packet_hash:raise ValueError('PAIR_DIRECT_RESERVATION_REQUIRED')
   p=self.intent_path(claim)
   if p.is_symlink() or not p.is_file():raise ValueError('PAIR_DIRECT_INTENT_REQUIRED')
   packet=json.loads(p.read_text());self._metadata(claim,packet)
   if digest(packet)!=packet_hash:raise ValueError('PAIR_DIRECT_INTENT_CHANGED')
   reservation=self.authority.prepare_publication(claim,packet_hash)
  guard=self.parent_guard(claim)
  if guard.get('requestId')!=record['parentId'] or guard.get('npcId')!=record.get('generationRequest',record['request'])['characterRef']['npcId']:raise ValueError('PAIR_DIRECT_GUARD_CHANGED')
  return {'reservation':reservation,'record':record,'parentGuard':guard}
