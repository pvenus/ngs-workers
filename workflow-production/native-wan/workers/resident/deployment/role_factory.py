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
    requests=HttpRequests(settings['requestUrl'],settings['workerId'],settings['requestToken'],journal_path=settings['claimJournal'],ownership_guard_timeout=30 if role in ('animation','postprocess') else None,checkpoint_timeout=30 if role in ('animation','postprocess') else None)
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
        def client(claim):return AnimationPublicationClient(settings['pdsUrl'],lambda:guard(claim),claim.request_id,animation_timeout_phases=role=='animation',context_diagnostics=role=='document',context_authorizer=(lambda path,packet:authorize_document_context(claim,drafts,path,packet)) if role=='document' else None)
        if role=='document':
            from ..backends.document_npc import NPCDocumentBackend,NPCContextStager
            from ..adapters.document_pds import DocumentDraftWorkspace
            backend=NPCDocumentBackend(settings['codexBinary'],settings['codexSha256'])
            from ..adapters.document_draft_http import HttpDrafts
            from ..adapters.document_context import authorize as authorize_document_context
            drafts=HttpDrafts(settings['requestUrl'],settings['workerId'],settings['requestToken'],journal_path=settings['claimJournal'])
            pds=PerClaimWorkspace(lambda claim:DocumentDraftWorkspace(settings['scratchRoot'],NPCContextStager(client(claim).json,client(claim),category_target=lambda _:drafts.authorize(claim)['categoryId']),drafts))
        elif role=='animation':
            from ..backends.animation import AnimationBackend
            from ..adapters.animation_workspace import AnimationWorkspace
            from ..adapters.animation_retained_http import HttpRetainedAnimation
            retained=HttpRetainedAnimation(settings['requestUrl'],settings['workerId'],settings['requestToken'],journal_path=settings['claimJournal'])
            backend=AnimationBackend(require('animation_config'),residency_root=settings['modelResidencyRoot'],
                keep_model_idle=bindings.get('animation_keep_model_idle',False),retained_port=retained)
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
    elif role=='image':
        from ..backends.portrait_pair import PortraitPairBackend
        from ..adapters.portrait_pair_workspace import PortraitPairWorkspace
        from ..adapters.portrait_pair_http import HttpPortraitPairs
        from .image_music import portrait_source
        pairs=HttpPortraitPairs(settings['requestUrl'],settings['workerId'],settings['requestToken'],journal_path=settings['claimJournal'])
        guard=bindings.get('ownership_guard',requests.ownership_guard)
        backend=PortraitPairBackend(require('portrait_pair_runner_factory'),require('portrait_pair_module'),require('portrait_alpha_validator'))
        def image_space(claim):
            direct=None
            if settings.get('portraitDirectPDS') is True:
                from ..adapters.portrait_pair_pds import DirectPortraitPairClient
                factory=bindings.get('portrait_direct_pds_factory')
                direct=factory(claim) if factory is not None else DirectPortraitPairClient(settings['pdsUrl'],ownership_guard=guard)
                if not callable(getattr(direct,'publish',None)):raise ValueError('PAIR_DIRECT_PDS_PORT_REQUIRED')
            return PortraitPairWorkspace(settings['scratchRoot'],pairs,portrait_source(pds_url=settings['pdsUrl'],guard_provider=lambda:guard(claim),stage_request_id=claim.request_id),require('cleanup'),direct_pds=direct)
        from types import MappingProxyType
        native_routes={'portrait':image_space}
        if 'pose_direction_runner_factory' in bindings:
            from ..backends.pose_direction import PoseDirectionBackend
            from ..adapters.pose_direction_http import HttpPoseDirections
            from ..adapters.pose_direction_workspace import PoseDirectionWorkspace,PoseDirectionSource
            from ..adapters.pds_animation import AnimationPublicationClient
            pose=HttpPoseDirections(settings['requestUrl'],settings['workerId'],settings['requestToken'],journal_path=settings['claimJournal'])
            backend=ImageBackendDispatch(backend,PoseDirectionBackend(require('pose_direction_runner_factory'),require('portrait_pair_module'),require('pose_worker_module'),require('pose_alpha_validator')))
            portrait_space=image_space
            def pose_space(claim):
                client=AnimationPublicationClient(settings['pdsUrl'],lambda:guard(claim),claim.request_id,animation_timeout_phases=role=='animation')
                direct=None
                if settings.get('poseDirectPDS') is True:
                    from ..adapters.pose_direction_pds import DirectPoseDirectionClient
                    factory=bindings.get('pose_direct_pds_factory')
                    direct=factory(claim) if factory is not None else DirectPoseDirectionClient(settings['pdsUrl'],ownership_guard=guard)
                    if not callable(getattr(direct,'publish',None)):raise ValueError('POSE_DIRECT_PDS_PORT_REQUIRED')
                return PoseDirectionWorkspace(settings['poseScratchRoot'],pose,PoseDirectionSource(pose,client.asset),require('cleanup'),direct_pds=direct)
            native_routes['pose']=pose_space
            def image_space(claim):
                return (pose_space if claim.parameters.get('schemaVersion')=='ngs.pose-direction-request.v1' else portrait_space)(claim)
        pds=PerClaimWorkspace(image_space)
        # Native admission inspects the same constructors before any child DTO
        # exists. These read-only ports do not fabricate claims or stage files.
        pds.native_routes=MappingProxyType(native_routes)
    elif role=='music':
        from .image_music import media_workspace,music_backend
        from ..adapters.media_source_http import HttpMediaSource
        source_reader=bindings.get('source_reader')
        if source_reader is None:source_reader=HttpMediaSource(settings['requestUrl'],settings['workerId'],settings['requestToken'],journal_path=settings['claimJournal'])
        if not callable(source_reader):raise ValueError('MUSIC_SOURCE_PORT_REQUIRED')
        pds=media_workspace(role=role,root=settings['scratchRoot'],pds_url=settings['pdsUrl'],pds_token=settings['pdsToken'],source_reader=source_reader,cleanup=require('cleanup'))
        backend=music_backend(**require('music_config'))
    else:raise ValueError('WORKER_ROLE_UNSUPPORTED')
    from ..adapters.terminal_evidence_http import HttpTerminalEvidence
    from ..terminal_evidence import TrustedEvidenceReporter,SourcePin
    pins=require('terminal_pins')
    if not pins or any(not isinstance(pin,SourcePin) for pin in pins):raise ValueError('TERMINAL_SOURCE_PINS_REQUIRED')
    reporter=TrustedEvidenceReporter(HttpTerminalEvidence(settings['requestUrl'],settings['workerId'],settings['requestToken'],journal_path=settings['claimJournal']),settings['terminalBackendId'],role,pins)
    if role=='animation':
        from ..adapters.animation_retained_http import RetainedReporter
        reporter=RetainedReporter(reporter,retained)
    legacy=Ports(requests,pds,backend,residency,reporter)
    enabled=settings.get('nativePNG',False)
    if type(enabled)is not bool:raise ValueError('NATIVE_PNG_OPT_IN_INVALID')
    if not enabled:return legacy
    if role not in ('animation','postprocess'):raise ValueError('NATIVE_ROLE_UNSUPPORTED')
    native_bindings=require('native_bindings')
    if callable(native_bindings):native_bindings=native_bindings(requests)
    if type(native_bindings)is not dict:raise ValueError('WORKER_TRUSTED_BINDING_REQUIRED')
    from .native_png_ports import build_native_ports
    from .native_png_dispatch import mixed_native_ports
    native=build_native_ports(role,settings,{**native_bindings,'requests':requests,'residency':residency,'evidence':reporter})
    return mixed_native_ports(legacy,native,enabled=True)

class FrameBackendDispatch:
    requires_model=True
    def __init__(self,half,full):self.half,self.full=half,full
    def requires_model_for(self,claim):return claim.parameters.get('scalePreset','full')!='half'
    def set_model_lease(self,lease):self.full.set_model_lease(lease)
    def load(self):self.full.load()
    def unload(self):self.full.unload()
    def execute(self,claim,workspace,cancel):
        return (self.full if self.requires_model_for(claim) else self.half).execute(claim,workspace,cancel)

    def execution_evidence(self,claim):
        return (self.full if self.requires_model_for(claim) else self.half).execution_evidence(claim)
    def model_unload_evidence(self,lease):return self.full.model_unload_evidence(lease)


class ImageBackendDispatch:
    def __init__(self,pair,pose):self.pair,self.pose=pair,pose
    def target(self,claim):return self.pose if claim.parameters.get('schemaVersion')=='ngs.pose-direction-request.v1' else self.pair
    def load(self):self.pair.load();self.pose.load()
    def unload(self):self.pair.unload();self.pose.unload()
    def execute(self,claim,workspace,cancel):return self.target(claim).execute(claim,workspace,cancel)
    def execution_evidence(self,claim):return self.target(claim).execution_evidence(claim)
