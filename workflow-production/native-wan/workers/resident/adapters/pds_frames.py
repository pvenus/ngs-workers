"""Actual PDS animation-frame publication API; preserves its existing wire contract.

PDS owns Filex writes. This client never opens Filex paths. Publication uses the
existing JSON artifact envelope API, unlike the NPC category raw-file API.
Large multi-asset commits have a separate bounded POST timeout; reads stay short.
A timeout still means unknown publication, never permission to regenerate.
"""
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.request
from .pds_category_files import CategoryFileClient,encode,header


CONTEXT_READ_CONFLICTS=frozenset(('WORKER_CONTEXT_NPC_CHANGED','WORKER_CONTEXT_SOURCE_CHANGED','WORKER_CONTEXT_MASTER_CHANGED'))

class FramePublicationClient(CategoryFileClient):
    def __init__(self,base_url,guard_provider,stage_request_id,*,timeout=15,publish_timeout=120,context_diagnostics=False,context_authorizer=None):
        super().__init__(base_url,guard_provider,stage_request_id,timeout=timeout)
        if type(publish_timeout) not in (int,float) or not 0<publish_timeout<=300:
            raise ValueError('FRAME_PUBLISH_TIMEOUT_INVALID')
        self.publish_timeout=publish_timeout
        self.context_diagnostics=context_diagnostics is True
        if context_authorizer is not None and not callable(context_authorizer):raise ValueError('DOCUMENT_CONTEXT_AUTHORIZER_REQUIRED')
        self.context_authorizer=context_authorizer

    def _frame_call(self,path,packet=None):
        phase=('catalog' if path.startswith('/api/worker-context/catalog?') and packet is None
               else 'read' if path=='/api/worker-context/read' and packet is not None else None)
        if not self.context_diagnostics or phase is None:
            return self._frame_call_impl(path,packet)
        try: started=time.monotonic()
        except BaseException: return self._frame_call_impl(path,packet)
        def observe(event):
            # Diagnostic failure must never affect the original transport.
            try:
                if not re.fullmatch(r'[A-Za-z0-9._:-]{1,200}',self.stage):return
                print(json.dumps({'event':'PLANNING_PDS_CONTEXT','phase':phase,'state':event,
                                  'requestId':self.stage,'elapsedMs':round((time.monotonic()-started)*1000,3)},sort_keys=True),file=sys.stderr,flush=True)
            except BaseException: pass
        observe('started')
        try:
            result=self._frame_call_impl(path,packet,observe)
            observe('completed')
            return result
        except BaseException:
            observe('failed')
            raise

    def _frame_call_impl(self,path,packet=None,observe=None):
        context=(packet is None and path.startswith('/api/worker-context/catalog?') or packet is not None and path=='/api/worker-context/read')
        if context and self.context_authorizer is not None:
            if observe is not None:observe('authorization_started')
            try:self.context_authorizer(path,packet)
            except BaseException:
                if observe is not None:observe('authorization_failed')
                raise
            if observe is not None:observe('authorization_completed')
            heads={'Content-Type':'application/json'}
        else:
            guard=self._guard()
            heads={'X-NPC-Ownership-Guard':header(guard),'X-NPC-Stage-Request-Id':self.stage,
                   'Content-Type':'application/json'}
        raw=encode(packet) if packet is not None else None
        if raw is not None and len(raw)>256*1024*1024:raise ValueError('FRAME_PACKET_TOO_LARGE')
        req=urllib.request.Request(self.base+path,data=raw,headers=heads)
        try:
            with self.opener.open(req,timeout=self.publish_timeout if packet is not None else self.timeout) as response:
                if observe is not None:observe('headers')
                data=response.read(16*1024*1024+1)
                if len(data)>16*1024*1024:raise ValueError('FRAME_RECEIPT_TOO_LARGE')
                return json.loads(data)
        except urllib.error.HTTPError as exc:
            if exc.code==404 and packet is None:return None
            try:code=json.loads(exc.read(65536)).get('error')
            except (ValueError,AttributeError):code=None
            context_conflict=(exc.code==409 and path=='/api/worker-context/read' and packet is not None and isinstance(code,str) and code in CONTEXT_READ_CONFLICTS)
            if not context_conflict and (not isinstance(code,str) or not re.fullmatch(r'NPC_[A-Z0-9_]{1,150}',code)):
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
        if not re.fullmatch(r'[a-f0-9]{64}\.png',asset_id):raise ValueError('FRAME_ASSET_ID_INVALID')
        req=urllib.request.Request(self.base+'/assets/portraits/'+asset_id,headers={
            'X-NPC-Ownership-Guard':header(self._guard()),'X-NPC-Stage-Request-Id':self.stage})
        with self.opener.open(req,timeout=self.timeout) as response:
            raw=response.read(64*1024*1024+1)
        if len(raw)>64*1024*1024 or hashlib.sha256(raw).hexdigest()+'.png'!=asset_id:
            raise ValueError('FRAME_PDS_ASSET_HASH_MISMATCH')
        return raw
