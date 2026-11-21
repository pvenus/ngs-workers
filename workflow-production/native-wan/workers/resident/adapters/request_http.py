"""Worker-side HTTP client. Contains no SQLite imports or authority access."""
from dataclasses import asdict
import json
import os
import re
import secrets
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request
from ..contracts import Claim
from .pds_category_files import NoRedirect


class HttpRequests:
    def __init__(self,base_url,worker_id,token,*,journal_path,timeout=5,ownership_guard_timeout=None,checkpoint_timeout=None):
        u=urllib.parse.urlsplit(base_url)
        if (u.scheme!='http' or u.hostname!='127.0.0.1' or u.port is None
                or u.username or u.password or u.path not in ('','/') or u.query or u.fragment):
            raise ValueError('REQUEST_API_LOOPBACK_REQUIRED')
        if not 0<timeout<=5 or not worker_id or not token:
            raise ValueError('REQUEST_API_CONFIG_INVALID')
        if ownership_guard_timeout is not None and (type(ownership_guard_timeout) not in (int,float) or not 0<ownership_guard_timeout<=30):
            raise ValueError('REQUEST_GUARD_TIMEOUT_INVALID')
        if checkpoint_timeout is not None and (type(checkpoint_timeout) not in (int,float) or not 0<checkpoint_timeout<=30):
            raise ValueError('REQUEST_CHECKPOINT_TIMEOUT_INVALID')
        self.checkpoint_timeout=timeout if checkpoint_timeout is None else checkpoint_timeout
        self.ownership_guard_timeout=timeout if ownership_guard_timeout is None else ownership_guard_timeout
        self.base,self.worker_id,self.token,self.timeout=base_url.rstrip('/'),worker_id,token,timeout
        self.journal=Path(journal_path)
        if not self.journal.is_absolute() or self.journal.is_symlink() or not self.journal.parent.is_dir():
            raise ValueError('CLAIM_JOURNAL_REQUIRED')
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())

    def _call(self,operation,body):
        request=urllib.request.Request(self.base+'/v1/workers/'+operation,
            data=json.dumps(body,separators=(',',':')).encode(),
            headers={'Content-Type':'application/json','Authorization':'Bearer '+self.token})
        try:
            with self.opener.open(request,timeout=self.ownership_guard_timeout if operation in ('ownership-guard','native-workspace-refresh') else self.checkpoint_timeout if operation=='checkpoint' else self.timeout) as response:
                raw=response.read(1024*1024+1)
                if len(raw)>1024*1024:raise RuntimeError('REQUEST_API_RESPONSE_TOO_LARGE')
                return json.loads(raw)
        except urllib.error.HTTPError as exc:
            # Never expose response body or ownership tokens in error messages.
            raise RuntimeError('REQUEST_API_HTTP_'+str(exc.code)) from None

    def _save_key(self,key):
        temporary=self.journal.with_name(self.journal.name+'.'+secrets.token_hex(8))
        fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as stream:
            json.dump({'workerId':self.worker_id,'claimKey':key},stream)
            stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,self.journal)
        fd=os.open(self.journal.parent,os.O_RDONLY)
        try:os.fsync(fd)
        finally:os.close(fd)

    def _claim_key(self):
        if self.journal.exists():
            value=json.loads(self.journal.read_text())
            if value['workerId']!=self.worker_id:raise ValueError('CLAIM_JOURNAL_WORKER_MISMATCH')
            if value['claimKey']:return value['claimKey']
        key=secrets.token_hex(16);self._save_key(key);return key

    def claim(self,profile,worker_id):
        if worker_id!=self.worker_id:raise ValueError('REQUEST_API_WORKER_MISMATCH')
        body={'workerId':worker_id,'role':profile.role,'claimKey':self._claim_key()}
        # One bounded transport retry uses the same durable key, never a fresh claim.
        try:result=self._call('claim',body)
        except (OSError,TimeoutError):result=self._call('claim',body)
        if result['claim'] is None:
            self._save_key(None);return None
        value=result['claim'];value['input_refs']=tuple(value['input_refs'])
        claim=Claim(**value);claim.validate(profile);return claim

    def ownership_guard(self,claim):
        return self._call('ownership-guard',{'claim':asdict(claim)})['guard']

    def refresh_native_workspace(self,claim):
        value=self._call('native-workspace-refresh',{'claim':asdict(claim)})
        if (type(value)is not dict or set(value)!={'registered','requestId','attemptId','fencingToken','inventoryHash'}
            or value['registered'] is not True
            or (value['requestId'],value['attemptId'],value['fencingToken'])!=(claim.request_id,claim.attempt_id,claim.fencing_token)
            or not isinstance(value['inventoryHash'],str) or not re.fullmatch('[a-f0-9]{64}',value['inventoryHash'])):
            raise ValueError('NATIVE_WORKSPACE_REFRESH_RESPONSE')
        return value

    def renew(self,claim):return self._call('renew',{'claim':asdict(claim)})['renewed'] is True
    def mark_publication_started(self,claim):
        self._call('publication-started',{'claim':asdict(claim)})
    def checkpoint(self,claim,receipt):
        self._call('checkpoint',{'claim':asdict(claim),'receipt':asdict(receipt)})
    def complete(self,claim,receipt,cleanup_done):
        self._call('complete',{'claim':asdict(claim),'receipt':asdict(receipt),'cleanupDone':cleanup_done})
    def fail(self,claim,code,cleanup_done):
        self._call('fail',{'claim':asdict(claim),'code':code,'cleanupDone':cleanup_done})
    def uncertain(self,claim,code):self._call('uncertain',{'claim':asdict(claim),'code':code})
    def execution_unknown(self,claim,code):
        self._call('execution-unknown',{'claim':asdict(claim),'code':code})
    def release_resource(self,claim):
        self._call('release',{'claim':asdict(claim)})
        self._save_key(None)
