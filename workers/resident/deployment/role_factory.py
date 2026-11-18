"""Concrete role wiring from operator config and reviewed service bindings.

Bindings are installed by the pinned factory, never loaded from a request DTO.
Credentials only go to loopback clients, not model prompts or provenance.
"""
from .composition import Ports
from ..adapters.request_http import HttpRequests
from ..adapters.model_residency_http import HttpModelResidency

class PerClaimWorkspace:
    def __init__(self,builder):self.builder=builder;self.active=None;self.claim=None
    def stage(self,claim):
        if self.active is not None:raise ValueError('WORKER_WORKSPACE_ALREADY_ACTIVE')
        self.active=self.builder(claim);self.claim=claim
        return self.active.stage(claim)
    def publish(self,claim,workspace,artifacts):
        if claim!=self.claim or self.active is None:raise ValueError('WORKER_WORKSPACE_CLAIM_CHANGED')
        return self.active.publish(claim,workspace,artifacts)
    def cleanup(self,claim,workspace):
        if self.active is None or claim!=self.claim:return False
        try:return self.active.cleanup(claim,workspace)
        finally:
            # Durable server checkpoint owns cleanup_pending; keep files, not
            # this worker pointer, so a different formal claim can proceed.
            self.active=None;self.claim=None

def build_role_ports(role,settings,bindings):
    required={'requestUrl','requestToken','workerId','claimJournal','scratchRoot','pdsUrl'}
    if not required<=settings.keys():raise ValueError('WORKER_DEPLOYMENT_SETTINGS_MISSING')
    requests=HttpRequests(settings['requestUrl'],settings['workerId'],settings['requestToken'],journal_path=settings['claimJournal'])
    def require(name):
        value=bindings.get(name)
        if value is None:raise ValueError('WORKER_TRUSTED_BINDING_REQUIRED')
        return value
    residency=None
    if role in ('animation','music') or role=='postprocess' and 'full_frame_config' in bindings:
        residency=HttpModelResidency(settings['modelUrl'],settings['workerId'],settings['modelToken'],journal_path=settings['modelJournal'])
    if role in ('document','animation','postprocess'):
        from ..adapters.pds_animation import AnimationPublicationClient
        guard=bindings.get('ownership_guard',requests.ownership_guard)
        def client(claim):return AnimationPublicationClient(settings['pdsUrl'],lambda:guard(claim),claim.request_id)
        if role=='document':
            from ..backends.document_npc import NPCDocumentBackend,NPCContextStager
            from ..adapters.document_pds import DocumentDraftWorkspace
            backend=NPCDocumentBackend(settings['codexBinary'],settings['codexSha256'])
            from ..adapters.document_draft_http import HttpDrafts
            drafts=HttpDrafts(settings['requestUrl'],settings['workerId'],settings['requestToken'],journal_path=settings['claimJournal'])
            pds=PerClaimWorkspace(lambda claim:DocumentDraftWorkspace(settings['scratchRoot'],NPCContextStager(client(claim).json,client(claim),category_target=lambda _:drafts.authorize(claim)['categoryId']),drafts))
        elif role=='animation':
            from ..backends.animation import AnimationBackend
            from ..adapters.animation_workspace import AnimationWorkspace
            backend=AnimationBackend(require('animation_config'),residency_root=settings['modelResidencyRoot'])
            pds=PerClaimWorkspace(lambda claim:AnimationWorkspace(settings['scratchRoot'],client(claim)))
        else:
            from ..backends.frame_postprocess import FramePostprocessBackend
            from ..adapters.frame_workspace import HalfFrameWorkspace
            from ..adapters.pds_frames import FramePublicationClient
            if 'full_frame_config' in bindings:
                from ..backends.frame_full import FullFrameBackend
                from ..adapters.frame_full_workspace import FullFrameWorkspace,FullFrameClient
                backend=FrameBackendDispatch(FramePostprocessBackend(),FullFrameBackend(**bindings['full_frame_config']))
                def frame_space(claim):
                    if claim.parameters.get('scalePreset','full')=='half':
                        return HalfFrameWorkspace(settings['scratchRoot'],FramePublicationClient(settings['pdsUrl'],lambda:guard(claim),claim.request_id))
                    return FullFrameWorkspace(settings['scratchRoot'],FullFrameClient(settings['pdsUrl'],lambda:guard(claim),claim.request_id))
                pds=PerClaimWorkspace(frame_space)
            else:
                backend=FramePostprocessBackend()
                pds=PerClaimWorkspace(lambda claim:HalfFrameWorkspace(settings['scratchRoot'],FramePublicationClient(settings['pdsUrl'],lambda:guard(claim),claim.request_id)))
    elif role in ('image','music'):
        from .image_music import media_workspace,image_backend,music_backend
        pds=media_workspace(role=role,root=settings['scratchRoot'],pds_url=settings['pdsUrl'],pds_token=settings['pdsToken'],source_reader=require('source_reader'),cleanup=require('cleanup'))
        if role=='image':backend=image_backend(binary=settings['codexBinary'],runner_builder=require('native_runner_builder'))
        else:backend=music_backend(**require('music_config'))
    else:raise ValueError('WORKER_ROLE_UNSUPPORTED')
    return Ports(requests,pds,backend,residency)

class FrameBackendDispatch:
    requires_model=True
    def __init__(self,half,full):self.half,self.full=half,full
    def requires_model_for(self,claim):return claim.parameters.get('scalePreset','full')!='half'
    def set_model_lease(self,lease):self.full.set_model_lease(lease)
    def load(self):self.full.load()
    def unload(self):self.full.unload()
    def execute(self,claim,workspace,cancel):
        return (self.full if self.requires_model_for(claim) else self.half).execute(claim,workspace,cancel)
