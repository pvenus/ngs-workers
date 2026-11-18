"""PDS workspace port for formal HALF reprocessing of a pinned full frame set.

Full video extraction/alpha inference is intentionally unsupported here. No
original Filex files are accessed directly. Every input/output crosses PDS API.
"""
import base64
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import shutil
from ..contracts import Receipt
from ..runtime import ReconciliationRequired
from ..backends.frame_postprocess import DIRECTIONS
from .pds_category_files import encode


def digest(value):return hashlib.sha256(encode(value)).hexdigest()

def save(path,value):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as stream:stream.write(encode(value));stream.flush();os.fsync(stream.fileno())


class HalfFrameWorkspace:
    def __init__(self,root,client):
        self.root=Path(root)
        if not self.root.is_absolute() or self.root.resolve(strict=True)!=self.root or not self.root.is_dir():
            raise ValueError('WORKER_SCRATCH_ROOT_REQUIRED')
        self.client=client
    def _folder(self,claim):return self.root/digest([claim.request_id,claim.attempt_id])
    def _npc(self,current,request):
        matches=[n for n in current['document']['characters'] if n['id']==request['npcId']]
        if len(matches)!=1:raise ValueError('FRAMES_NPC_REQUIRED')
        return matches[0]
    def _source(self,npc,request):
        pin=request['sourceFrameSet'];source=npc.get('animationFrameSets',{}).get(pin['slotKey'])
        if (not isinstance(source,dict) or source.get('requestId')!=pin['requestId']
                or source.get('inputHash')!=pin['inputHash'] or digest(source)!=pin['contentHash']):
            raise ValueError('FRAMES_SOURCE_SET_CHANGED')
        return source
    def stage(self,claim):
        request=claim.parameters
        if request.get('scalePreset')!='half':raise ValueError('FULL_EXTRACTION_ADAPTER_NOT_CONNECTED')
        folder=self._folder(claim)
        if folder.exists() or folder.is_symlink():
            raise ReconciliationRequired('EXISTING_ATTEMPT_REQUIRES_RECONCILIATION')
        folder.mkdir(mode=0o700)
        save(folder/'claim.json',asdict(claim))
        current=self.client.current();npc=self._npc(current,request)
        if str(current['revision'])!=claim.expected_revision:raise ValueError('FRAME_STAGE_REVISION_CHANGED')
        source=self._source(npc,request)
        if set(source['directions'])!=set(DIRECTIONS):raise ValueError('FRAMES_SOURCE_DIRECTIONS_REQUIRED')
        target_key=request['posePreset']+'/'+request['presetId']+'/half'
        save(folder/'pin.json',{'creationBasis':npc.get('creationBasis'),
             'target':npc.get('animationFrameSets',{}).get(target_key)})
        directions={}
        for d,row in source['directions'].items():
            if len(row['frames'])!=row['frameCount'] or [f['index'] for f in row['frames']]!=list(range(row['frameCount'])):
                raise ValueError('FRAMES_SOURCE_COUNT_MISMATCH')
            raw_paths=[];alpha_paths=[]
            for frame in row['frames']:
                for field,paths in (('rawAssetId',raw_paths),('alphaAssetId',alpha_paths)):
                    aid=frame[field]
                    if not isinstance(aid,str) or len(aid)!=68 or not all(c in '0123456789abcdef' for c in aid[:64]) or not aid.endswith('.png'):
                        raise ValueError('FRAME_ASSET_ID_INVALID')
                    path=folder/aid
                    if not path.exists():
                        data=self.client.asset(aid)
                        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
                        with os.fdopen(fd,'wb') as stream:stream.write(data)
                    paths.append(str(path))
            directions[d]={'rawFrames':raw_paths,'alphaFrames':alpha_paths,'frameCount':row['frameCount'],
                'fps':row['fps'],'sourceAssetId':row['sourceAssetId'],
                'frameTimes':[f.get('sourceTimestamp',i/row['fps']) for i,f in enumerate(row['frames'])]}
        save(folder/'frame-inputs.json',{'requestId':claim.request_id,'attemptId':claim.attempt_id,
                                        'expectedRevision':claim.expected_revision,'directions':directions})
        return folder
    def publish(self,claim,workspace,artifacts):
        if workspace!=self._folder(claim):raise ValueError('FRAME_WORKSPACE_MISMATCH')
        request=claim.parameters
        manifest=json.loads((workspace/'frame-manifest.json').read_text())
        if manifest.get('requestId')!=claim.request_id or manifest.get('canvas',{}).get('scale')!=.5:
            raise ValueError('FRAME_OUTPUT_CONTRACT_INVALID')
        envelopes=[]
        for sha,name in manifest.pop('artifacts').items():
            p=Path(name)
            if p.is_symlink() or workspace not in p.resolve(strict=True).parents:
                raise ValueError('FRAME_OUTPUT_PATH_INVALID')
            raw=p.read_bytes()
            if hashlib.sha256(raw).hexdigest()!=sha:raise ValueError('FRAME_OUTPUT_HASH_INVALID')
            envelopes.append({'sha256':sha,'bytes':len(raw),'mimeType':'image/png',
                              'base64':base64.b64encode(raw).decode()})
        current=self.client.current();npc=self._npc(current,request);self._source(npc,request)
        pin=json.loads((workspace/'pin.json').read_text())
        key=request['posePreset']+'/'+request['presetId']
        if (npc.get('creationBasis')!=pin['creationBasis']
                or npc.get('animationFrameSets',{}).get(key+'/half')!=pin['target']):
            raise ValueError('FRAME_TARGET_OR_INCARNATION_CHANGED')
        manifest.update(posePreset=request['posePreset'],presetId=request['presetId'],scalePreset='half',
                        pixelScale=.5,sourceFrameSet=request['sourceFrameSet'],
                        processingPolicy='extract-alpha-pixel-half.v1',sourceVideoStatus={
                            'staleDirections':[d for d in DIRECTIONS if npc.get('animationSlots',{}).get(key+'/'+d,{}).get('assetId')!=manifest['directions'][d]['sourceAssetId']],
                            'policy':'pinned-full-frame-set-not-latest-videos'})
        packet={'requestId':claim.request_id,'projectId':request['projectId'],'npcId':request['npcId'],
                'expectedSourceVersion':current['sourceVersion'],'expectedCharacterHash':digest(npc),
                'manifest':manifest,'artifacts':envelopes,'trace':{'modelCalls':0,'inputTransport':'PDS',
                  'sourceFrameRequestId':request['sourceFrameSet']['requestId']}}
        save(workspace/'publication-intent.json',packet)
        receipt=self.client.publish(packet)
        save(workspace/'publication-receipt.json',receipt)
        return Receipt(claim.request_id,claim.attempt_id,claim.fencing_token,
                       tuple(a['sha256']+'.png' for a in envelopes),str(receipt['revision']),True)
    def recover(self,claim):
        """Service recovery read only: never reruns a backend or republishes."""
        folder=self._folder(claim)
        if not folder.exists():return None
        if folder.is_symlink():raise ValueError('FRAME_RECOVERY_WORKSPACE_INVALID')
        owner=folder/'claim.json';intent=folder/'publication-intent.json'
        if owner.is_symlink() or not owner.is_file() or json.loads(owner.read_text())!=json.loads(encode(asdict(claim))):
            raise ValueError('FRAME_RECOVERY_OWNER_MISMATCH')
        if not intent.exists():return None
        if intent.is_symlink():raise ValueError('FRAME_RECOVERY_INTENT_INVALID')
        packet=json.loads(intent.read_text())
        if packet.get('requestId')!=claim.request_id:raise ValueError('FRAME_RECOVERY_REQUEST_MISMATCH')
        receipt=self.client.receipt(claim.request_id)
        if receipt is None:return None
        self.client.verify(packet,receipt)
        if 'artifacts' in packet:refs=tuple(a['sha256']+'.png' for a in packet['artifacts'])
        else:refs=(receipt['assetId'],)
        return Receipt(claim.request_id,claim.attempt_id,claim.fencing_token,refs,str(receipt['revision']),True)

    def cleanup(self,claim,workspace):
        folder=self._folder(claim)
        if not folder.exists():return True
        if folder.is_symlink() or not (folder/'claim.json').is_file():return False
        if json.loads((folder/'claim.json').read_text())!=json.loads(encode(asdict(claim))):return False
        shutil.rmtree(folder)
        return not folder.exists()
