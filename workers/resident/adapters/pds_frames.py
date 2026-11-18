"""Actual PDS animation-frame publication API; preserves its existing wire contract.

PDS owns Filex writes. This client never opens Filex paths. Publication uses the
existing JSON artifact envelope API, unlike the NPC category raw-file API.
"""
import hashlib
import json
import re
import urllib.error
import urllib.request
from .pds_category_files import CategoryFileClient,encode,header


class FramePublicationClient(CategoryFileClient):
    def _frame_call(self,path,packet=None):
        guard=self._guard()
        heads={'X-NPC-Ownership-Guard':header(guard),'X-NPC-Stage-Request-Id':self.stage,
               'Content-Type':'application/json'}
        raw=encode(packet) if packet is not None else None
        if raw is not None and len(raw)>256*1024*1024:raise ValueError('FRAME_PACKET_TOO_LARGE')
        req=urllib.request.Request(self.base+path,data=raw,headers=heads)
        try:
            with self.opener.open(req,timeout=self.timeout) as response:
                data=response.read(16*1024*1024+1)
                if len(data)>16*1024*1024:raise ValueError('FRAME_RECEIPT_TOO_LARGE')
                return json.loads(data)
        except urllib.error.HTTPError as exc:
            if exc.code==404 and packet is None:return None
            try:code=json.loads(exc.read(65536)).get('error')
            except (ValueError,AttributeError):code=None
            if not isinstance(code,str) or not re.fullmatch(r'NPC_[A-Z0-9_]{1,150}',code):
                code='PDS_FRAME_HTTP_'+str(exc.code)
            raise ValueError(code) from None

    def receipt(self,request_id):
        if not re.fullmatch(r'[A-Za-z0-9._:-]{8,180}',request_id):raise ValueError('FRAME_REQUEST_ID_INVALID')
        value=self._frame_call('/v1/npcs/animation-frames/requests/'+request_id)
        if value is not None and (not isinstance(value,dict) or value.get('requestId')!=request_id
                or value.get('verified') is not True or value.get('source')!='filex'):
            raise ValueError('FRAME_RECEIPT_UNVERIFIED')
        return value

    def verify(self,packet,receipt):
        expected=hashlib.sha256(encode({k:v for k,v in packet.items()
            if k not in ('expectedSourceVersion','expectedCharacterHash','artifacts')})).hexdigest()
        manifest=packet['manifest']
        slot=manifest['posePreset']+'/'+manifest['presetId']+('/half' if manifest.get('scalePreset')=='half' else '')
        if (not isinstance(receipt,dict) or receipt.get('verified') is not True
                or receipt.get('source')!='filex' or receipt.get('requestId')!=packet['requestId']
                or receipt.get('npcId')!=packet['npcId'] or receipt.get('slotKey')!=slot
                or receipt.get('inputHash')!=expected
                or receipt.get('frameCount')!=sum(x['frameCount'] for x in manifest['directions'].values())):
            raise ValueError('FRAME_RECEIPT_BINDING_MISMATCH')
        return receipt

    def publish(self,packet):
        guard=self._guard()
        if packet.get('npcId')!=guard['npcId'] or packet.get('requestId')!=self.stage:
            raise ValueError('FRAME_PUBLISH_SCOPE_MISMATCH')
        existing=self.receipt(packet['requestId'])
        if existing is not None:return self.verify(packet,existing)
        result=self._frame_call('/v1/npcs/animation-frames/publish',packet)
        self.verify(packet,result)
        if self.receipt(packet['requestId'])!=result:raise ValueError('FRAME_RECEIPT_READBACK_MISMATCH')
        return result

    def current(self):
        return self._frame_call('/api/npcs')

    def asset(self,asset_id):
        if not re.fullmatch('[a-f0-9]{64}\\.png',asset_id):raise ValueError('FRAME_ASSET_ID_INVALID')
        req=urllib.request.Request(self.base+'/assets/portraits/'+asset_id,headers={
            'X-NPC-Ownership-Guard':header(self._guard()),'X-NPC-Stage-Request-Id':self.stage})
        with self.opener.open(req,timeout=self.timeout) as response:
            raw=response.read(64*1024*1024+1)
        if len(raw)>64*1024*1024 or hashlib.sha256(raw).hexdigest()+'.png'!=asset_id:
            raise ValueError('FRAME_PDS_ASSET_HASH_MISMATCH')
        return raw
