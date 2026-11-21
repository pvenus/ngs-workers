"""NPC candidate workspace preserving parent-owned CAS publication.

The trusted draft port must register/verify the existing parent's candidate.
This port never calls CategoryFileClient.replace or claims canonical publication.
No legacy route is activated until that parent bridge is supplied and tested.
"""
from dataclasses import asdict
import json
from pathlib import Path
import shutil
from ..contracts import Receipt
from ..runtime import ReconciliationRequired
from .frame_workspace import digest,save

class DocumentDraftWorkspace:
    def __init__(self,root,stager,drafts):
        self.root=Path(root)
        if not self.root.is_absolute()or self.root.resolve(strict=True)!=self.root or not self.root.is_dir():raise ValueError('WORKER_SCRATCH_ROOT_REQUIRED')
        self.stager,self.drafts=stager,drafts
    def _folder(self,claim):return self.root/digest([claim.request_id,claim.attempt_id])
    def _check(self,claim,folder):
        if folder!=self._folder(claim)or folder.is_symlink():raise ValueError('DOCUMENT_WORKSPACE_MISMATCH')
        p=folder/'claim.json'
        if p.is_symlink()or not p.is_file()or json.loads(p.read_text())!=json.loads(json.dumps(asdict(claim))):raise ValueError('DOCUMENT_WORKSPACE_OWNER')
    def stage(self,claim):
        # This admission must prove the original parent owns apply/publication.
        authority=self.drafts.authorize(claim)
        if not isinstance(authority,dict)or authority.get('requestId')!=claim.request_id or authority.get('publicationScope')!='request-draft-candidate' or authority.get('parentOwnsApply')is not True:raise ValueError('DOCUMENT_PARENT_AUTHORITY_REQUIRED')
        folder=self._folder(claim)
        if folder.exists()or folder.is_symlink():raise ReconciliationRequired('DOCUMENT_EXISTING_ATTEMPT')
        folder.mkdir(mode=0o700);save(folder/'claim.json',asdict(claim));save(folder/'parent.json',authority)
        self.stager.stage(claim,folder)
        return folder
    def publish(self,claim,workspace,artifacts):
        self._check(claim,workspace)
        if len(artifacts)!=1 or Path(artifacts[0])!=workspace/'response.json' or Path(artifacts[0]).is_symlink():raise ValueError('DOCUMENT_OUTPUT_SCOPE')
        value=json.loads(Path(artifacts[0]).read_text())
        parent=json.loads((workspace/'parent.json').read_text())
        packet={'requestId':claim.request_id,'attemptId':claim.attempt_id,'fencingToken':claim.fencing_token,
                'requestHash':digest(claim.parameters),'parent':parent,'output':value,
                'publicationScope':'request-draft-candidate','sourceApplied':False}
        save(workspace/'draft-intent.json',packet)
        self.drafts.submit(claim,packet)
        receipt=self.drafts.read(claim)
        if (not isinstance(receipt,dict)or receipt.get('verified')is not True
            or receipt.get('requestId')!=claim.request_id or receipt.get('attemptId')!=claim.attempt_id
            or receipt.get('fencingToken')!=claim.fencing_token or receipt.get('packetHash')!=digest(packet)
            or receipt.get('publicationScope')!='request-draft-candidate' or receipt.get('sourceApplied')is not False
            or not isinstance(receipt.get('draftRef'),str)or not receipt['draftRef']
            or not isinstance(receipt.get('revision'),str)or not receipt['revision']):raise ValueError('DOCUMENT_DRAFT_RECEIPT_UNVERIFIED')
        save(workspace/'draft-receipt.json',receipt)
        return Receipt(claim.request_id,claim.attempt_id,claim.fencing_token,(receipt['draftRef'],),receipt['revision'],True)
    def cleanup(self,claim,workspace):
        folder=self._folder(claim)
        if not folder.exists():return not folder.is_symlink()
        self._check(claim,folder)
        shutil.rmtree(folder)
        return not folder.exists()
