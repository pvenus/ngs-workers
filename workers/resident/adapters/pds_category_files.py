"""Existing PDS NPC category FILE transport, independent of NGS cwd/imports.

This is not the full resident PDS port: ownership admission, scratch registry and
publication verification remain distinct adapters. A write receipt is NOT a
verified publication receipt. No retries or direct Filex writes are performed.
"""
import base64
import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request

MAX_FILE = 2 * 1024 * 1024  # Existing server limit; not a new transport restriction.


def encode(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()


def header(value):
    text=base64.b64encode(encode(value)).decode()
    if len(text)>32768: raise ValueError('PDS_HEADER_TOO_LARGE')
    return text


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        raise ValueError('PDS_REDIRECT_DENIED')


class CategoryFileClient:
    def __init__(self, base_url, guard_provider, stage_request_id, *, timeout=15):
        parsed=urllib.parse.urlsplit(base_url)
        if (parsed.scheme!='http' or parsed.hostname!='127.0.0.1' or
            parsed.username is not None or parsed.password is not None or
            parsed.path not in ('','/') or parsed.query or parsed.fragment or
            parsed.port is None):
            raise ValueError('PDS_LOOPBACK_ENDPOINT_REQUIRED')
        if not 0<timeout<=30: raise ValueError('PDS_TIMEOUT_INVALID')
        if not re.fullmatch(r'[A-Za-z0-9._:-]{1,180}',stage_request_id):
            raise ValueError('PDS_STAGE_ID_INVALID')
        self.base=base_url.rstrip('/')
        self.guard_provider,self.stage=guard_provider,stage_request_id
        self.timeout=timeout
        # Ignore proxy environment; never redirect an ownership guard elsewhere.
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())

    def _guard(self):
        guard=self.guard_provider()
        if not isinstance(guard,dict) or not all(isinstance(guard.get(k),str) and guard[k]
                for k in ('npcId','generationId','requestId','leaseToken')):
            raise ValueError('PDS_ADMITTED_OWNERSHIP_GUARD_REQUIRED')
        return guard

    def _call(self,path,guard,raw=None,metadata=None):
        heads={'X-NPC-Ownership-Guard':header(guard),'X-NPC-Stage-Request-Id':self.stage}
        if raw is not None:
            heads.update({'Content-Type':'application/octet-stream','X-NPC-File-Replace':header(metadata)})
        request=urllib.request.Request(self.base+path,data=raw,headers=heads)
        try:
            with self.opener.open(request,timeout=self.timeout) as response:
                body=response.read(MAX_FILE+1)
                if len(body)>MAX_FILE: raise ValueError('PDS_RESPONSE_TOO_LARGE')
                return body,response.headers
        except urllib.error.HTTPError as exc:
            try: code=json.loads(exc.read(65536)).get('error')
            except (ValueError,AttributeError): code=None
            # Preserve domain failure codes without exposing raw response data.
            if not isinstance(code,str) or not re.fullmatch(r'(?:CATEGORY|NPC)_[A-Z0-9_]{1,150}',code):
                code='PDS_HTTP_'+str(exc.code)
            raise ValueError(code) from None

    def download(self,npc_id,category_id,expected_revision,mode='modify'):
        if mode not in ('modify','add') or type(expected_revision) is not int or expected_revision<1:
            raise ValueError('PDS_FILE_REQUEST_INVALID')
        guard=self._guard()
        if guard['npcId']!=npc_id: raise ValueError('PDS_NPC_GUARD_MISMATCH')
        query=urllib.parse.urlencode({'npcId':npc_id,'categoryId':category_id,
                                      'expectedRevision':expected_revision,'mode':mode})
        raw,heads=self._call('/v1/npc-category/file?'+query,guard)
        value=heads.get('X-NPC-File-Basis','')
        if len(value)>32768: raise ValueError('PDS_BASIS_INVALID')
        basis=json.loads(base64.b64decode(value,validate=True))
        if not isinstance(basis,dict) or any(basis.get(k)!=v for k,v in {
                'schemaVersion':'npc-category-file.v1','npcId':npc_id,'categoryId':category_id,
                'revision':expected_revision,'mode':mode,'requestId':guard['requestId'],
                'stageRequestId':self.stage}.items()):
            raise ValueError('PDS_BASIS_BINDING_MISMATCH')
        if hashlib.sha256(raw).hexdigest()!=basis.get('sha256'):
            raise ValueError('PDS_DOWNLOAD_HASH_MISMATCH')
        return raw,basis

    def replace(self,raw,metadata):
        if not isinstance(raw,bytes) or not 0<len(raw)<=MAX_FILE:
            raise ValueError('PDS_FILE_SIZE_INVALID')
        guard=self._guard()
        if (not isinstance(metadata,dict) or metadata.get('projectId')!='ProjectBS'
                or metadata.get('npcId')!=guard['npcId']):
            raise ValueError('PDS_REPLACE_SCOPE_INVALID')
        basis=metadata.get('basis',{})
        if basis.get('requestId')!=guard['requestId'] or basis.get('stageRequestId')!=self.stage:
            raise ValueError('PDS_REPLACE_STAGE_MISMATCH')
        body,_=self._call('/v1/npc-category/file-replace',guard,raw,metadata)
        result=json.loads(body)
        if (not isinstance(result,dict) or result.get('authority')!='filex'
                or result.get('transport')!='pds-file-replace.v1'
                or result.get('operationHash')!=metadata.get('operationHash')
                or result.get('categoryId')!=metadata.get('categoryId')):
            raise ValueError('PDS_WRITE_RECEIPT_MISMATCH')
        return result
