"""Inert, request-scoped assembly of the existing native ownership host.

All callable inputs are trusted deployment closures, never request payloads.
No connection/factory is retained; each factory is built inside the caller's
ThreadAuthority scope. Only the explicit returned install function is global.
"""
import hashlib
from pathlib import Path
from threading import Event
from .resident_native_host import ResidentNativeHost
from .resident_operation_proof import ResidentOperationAllocations, ResidentOperationRoutingProbe, bind_operation_factory
from .pose_animation_native_proof import MediaOperationScope, MediaMetadataAllocations, NativeMediaParentLifecycle, MediaParentLifecycleAdapter
from .document_image_native_proof import SingleBatchOperationScope, SingleBatchMetadataAllocations, NativeSingleBatchLifecycle
from .resident_frame_proof import checked

KINDS={'planning':('document','planning-attempt'),'portrait':('image','portrait-stage'),'pose':('image','pose-request'),'animation':('animation','animation-job')}

def native_bindings(common, bridge, model, terminal, domains, *, operations, resolve_request, original_guard):
    """operations has per-operation trusted configuration, not proof booleans.

Required: config_path/config_sha256, scratch_root/root_key, source_root/
source_pins, worker_config, workspace_for_claim/backend_for_claim/route_claims,
workspace_types/backend_types, session_for_parent. Single stages also require
record_reader; portrait requires canonical_result. Optional frames uses its
existing factory_for_guard and lifecycle. domains may be filled after this call;
late-bound closures must resolve real domain objects when invoked.
    """
    if bridge.common is not common or not operations or not set(operations)<=set(KINDS)|{'frames'}:
        raise ValueError('NATIVE_SITE_BINDINGS')
    specs={k:dict(v)for k,v in operations.items()}
    scopes={}; builders={}; lives={}
    def failure_reader(guard):
        fence=getattr(bridge,'failure_fence',None)
        if fence is None:raise ValueError('NATIVE_SITE_FAILURE_FENCE_REQUIRED')
        return fence.seal_for_failure(guard)
    for operation,spec in specs.items():
        if operation=='frames':
            if not callable(spec.get('factory_for_guard')):raise ValueError('NATIVE_SITE_FRAME_BINDING')
            builders[operation]=spec['factory_for_guard'];continue
        role,kind=KINDS[operation]
        if spec.get('role',role)!=role:raise ValueError('NATIVE_SITE_ROLE')
        required=('session_for_parent','workspace_for_claim','backend_for_claim','route_claims')
        if operation in ('planning','portrait'):required+=('record_reader',)
        if operation=='portrait':required+=('canonical_result',)
        if any(not callable(spec.get(k))for k in required):raise ValueError('NATIVE_SITE_READERS')
        if not isinstance(spec.get('config_sha256'),str)or len(spec['config_sha256'])!=64:raise ValueError('NATIVE_SITE_CONFIG_PIN')
        scope=(SingleBatchOperationScope(common,original_guard,spec['record_reader'],family=role)
               if operation in ('planning','portrait')else MediaOperationScope(common,original_guard,family=operation))
        scopes[operation]=scope
        def factory(g, op=operation, s=spec, scoped=scope, role=role, kind=kind):
            from local.services.npc_scratch_bootstrap import private_json,install_packaged_host
            from local.services.npc_workflow_proofs import HostReaders
            from local.services.npc_scratch_allocations import MediaAllocations
            from local.services.npc_scratch_nonmedia import PersistedStageAllocations
            from local.services.npc_scratch_allocation_host import HostReports
            from local.services.npc_scratch_completion import completion_reader
            path=checked(s['config_path'])
            if hashlib.sha256(path.read_bytes()).hexdigest()!=s['config_sha256']:raise ValueError('NATIVE_SITE_CONFIG_CHANGED')
            c=private_json(path)
            if c.get('registry',{}).get('producers')!=[op]:raise ValueError('NATIVE_SITE_PRODUCER')
            readers=HostReaders(common,c['serviceRoots'])
            if readers.admission(g['requestId'])!=g or original_guard(g['requestId'])!=g:raise ValueError('NATIVE_SITE_ORIGINAL_GUARD')
            scoped(g)
            issued=ResidentOperationAllocations(bridge,scoped,role=role,producer=op,kind=kind,scratch_root=s['scratch_root'],root_key=s['root_key'],managed_root=c['roots'][s['root_key']],worker_id=s['worker_config']['workerId'])
            if op in ('pose','animation'):
                prior=MediaAllocations(common,{p:readers.roots[p]for p in MediaAllocations.TABLES},c['mediaRootKeys'],readers.admission)
                allocations=MediaMetadataAllocations(issued,prior,scoped)
            else:
                prior=PersistedStageAllocations(common,c['stageDomains'],readers.admission)
                allocations=SingleBatchMetadataAllocations(issued,prior,scoped,s.get('canonical_result'))
            probe=ResidentOperationRoutingProbe(allocations,source_root=s['source_root'],source_pins=s['source_pins'],worker_config=s['worker_config'],workspace_for_claim=s['workspace_for_claim'],backend_for_claim=s['backend_for_claim'],workspace_types=s['workspace_types'],backend_types=s['backend_types'],route_claims=s['route_claims'],host_id=c['hostId'],registry=c['registry'])
            def legacy(g):
                from .request_selector import digest
                directory=c.get('legacyEvidenceDirectory')
                if directory:
                    p=checked(directory)/(digest([g['npcId'],g['generationId']])+'.json')
                    if p.exists()or p.is_symlink():return private_json(p)
                return {'complete':False,'unresolvedPaths':['unverified-legacy']}
            reports=HostReports(c['hostId'],c['registry'],c['roots'],allocations,probe,legacy)
            built=[]
            def capture(value):built.append(bind_operation_factory(value,allocations))
            install_packaged_host(path,common=common,admission_reader=readers.admission,publication_reader=readers.publication,allocation_reader=allocations,host_reports=reports,completion_reader=completion_reader(readers,s.get('call')),call=s.get('call'),factory_sink=capture,install=False)
            if len(built)!=1:raise ValueError('NATIVE_SITE_FACTORY_COUNT')
            return built[0]
        builders[operation]=factory
        def by_id(rid, build=factory):return build(original_guard(rid))
        retry_options={'released_session_for_parent':spec.get('released_session_for_parent')}if operation in ('pose','animation')else{}
        lifecycle=(NativeSingleBatchLifecycle if operation in ('planning','portrait')else NativeMediaParentLifecycle)(scope,spec['session_for_parent'],by_id,failure_reader=failure_reader,**retry_options)
        lives[role if operation in ('planning','portrait')else operation]=lifecycle
    host=ResidentNativeHost(resolve_request=resolve_request,original_guard=original_guard,builders=builders)
    loop=NativeCompletionLoop(common,resolve_request,dict(lives))
    if 'animation'in lives:lives['animation']=MediaParentLifecycleAdapter(lives['animation'])
    if 'frames'in specs:
        from ..adapters.media_ownership_lifecycle import MediaOwnershipLifecycle
        def verified(output,guard):
            from local.services.npc_scratch import verified as native_verified
            return native_verified(output,guard)
        lives['postprocess']=MediaOwnershipLifecycle(configure=host.configure,producer=host.producer,verified=verified,failure_reader=failure_reader)
    def failure_guard_reader(rid):
        binding=resolve_request(rid)
        if binding.operation not in builders:raise ValueError('NATIVE_SITE_FAILURE_OPERATION')
        guard=original_guard(rid)
        if not isinstance(guard,dict)or guard.get('requestId')!=rid:raise ValueError('NATIVE_SITE_FAILURE_GUARD')
        return guard
    def failure_operation_scope(guard):
        if failure_guard_reader(guard['requestId'])!=guard:raise ValueError('NATIVE_SITE_FAILURE_GUARD_CHANGED')
        operation=resolve_request(guard['requestId']).operation
        if operation=='frames':return None # existing strict standalone-frame snapshot
        return scopes[operation](guard)
    return {'failure_guard_reader':failure_guard_reader,'failure_operation_scope':failure_operation_scope,'failure_publication_reader':lambda claim,expected:live_settled_publication(domains,claim,expected),'host':host,'lifecycles':lives,'animation_adapter':lives.get('animation'),'completion_loop':loop,'configure':host.configure,'install':host.install,'factories':builders,'scopes':scopes}

class NativeCompletionLoop:
    def __init__(self,common,resolve,lifecycles):
        self.common,self.resolve,self.lifecycles=common,resolve,lifecycles
        self.stop=Event();self.last_error=None
    def tick(self):
        output=[]
        for row in self.common.db.execute("SELECT request_id FROM common_requests WHERE parent_id IS NULL AND state IN ('completed','failed','blocked','conflict') ORDER BY created_at,request_id").fetchall():
            rid=row['request_id']
            try:
                op=self.resolve(rid).operation
                key={'planning':'document','portrait':'image'}.get(op,op)
                # Existing frame lifecycle has service/session signature and its
                # own observer. Never reinterpret its ownership here.
                if key not in self.lifecycles or op=='frames':continue
                output.append((rid,'released'if self.lifecycles[key].finalize(rid)else'pending'))
            except Exception as exc:self.last_error=type(exc).__name__;output.append((rid,'pending'))
        return output
    def serve(self):
        while not self.stop.is_set():
            try:self.tick()
            except Exception as exc:self.last_error=type(exc).__name__
            self.stop.wait(1)


def live_settled_publication(domains,claim,expected):
    """Re-read existing service's PDS receipt, never publish or rebuild a draft."""
    if not isinstance(expected,dict)or not expected:raise ValueError('NATIVE_FAILURE_EXPECTED_RECEIPT')
    if claim.capability=='image.generate':
        image=domains['image']
        schema=claim.parameters.get('schemaVersion')
        if schema=='ngs.pose-direction-request.v1':
            reader=getattr(image['pose_direction_service'],'settled_receipt_reader',None)
            if not callable(reader):raise ValueError('NATIVE_SETTLED_POSE_READER_REQUIRED')
            value=reader(claim)
        elif schema=='npc-workflow-portrait-single.v1':
            value=image['portrait_pair_service'].read(claim)['reservedReceipt']['canonicalReceipt']
        else:raise ValueError('NATIVE_FAILURE_IMAGE_ROUTE')
    elif claim.capability=='animation.generate':
        reader=domains['animation'].get('settled_receipt_reader')
        if not callable(reader):raise ValueError('NATIVE_SETTLED_ANIMATION_READER_REQUIRED')
        value=reader(claim)
    elif claim.capability=='frames.extract':
        domain=domains['animation'if claim.capability=='animation.generate'else'postprocess']
        # Original claim binding is checked by the factory workspace resolver;
        # receipt() is the existing PDS GET, not local journal trust.
        workspace=domain['workspace_for_claim'](claim)
        value=workspace.client.receipt(claim.request_id)
    else:
        # Document's service journal is a draft, not an origin publication.
        # Never elevate that local journal to a settled Filex proof.
        raise ValueError('NATIVE_FAILURE_LIVE_PUBLICATION_UNSUPPORTED')
    if not isinstance(value,dict)or value!=expected:raise ValueError('NATIVE_FAILURE_PDS_RECEIPT_CHANGED')
    return value
