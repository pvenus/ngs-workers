"""Five-role Request composition; no DB opens, model loads or native install at build.

The independent workflow process uses the gateway only. Metadata skeleton remains
on the legacy consumer. Native builders and shared OPS predicates are pinned site
ports, not request-controlled values or permissive fallback callbacks.
"""
import json
from threading import RLock
from .composite_domain import composite_domain_factory,LoopGroup
from .document_site import document_domain_factory
from .image_music_site import (portrait_pair_domain_factory,combined_image_domain_factory,
                               image_music_domain_factory)
from .pose_direction_site import pose_direction_domain_factory
from .animation_frame_site import animation_frame_domain_factory,postprocess_domain_factory
from .workflow_gateway_site import gateway_from_pinned_sources,retry_loop_from_pinned_sources
from .pose_batch_loop import PoseBatchLoop
from ..adapters.external_parent_runner import ExternalParentRunner


def full_domain_factory(sources,*,spec,handoff,preflight,native_bindings,cleanup,music_admitted):
    """native_bindings(common,bridge,model,terminal,domains) returns inert ports.

    Required ports: lifecycles for document/image/pose/animation/postprocess,
    install(), completion_loop. domains is populated before any callback runs.
    Native installation occurs only at explicit host startup, never preflight.
    """
    if not all(callable(v) for v in (preflight,native_bindings,cleanup,music_admitted)):
        raise ValueError('FULL_SITE_TRUSTED_PORTS_REQUIRED')
    for key in ('workflow_member_owned','workflow_execution_owned','standalone_frame'):
        if not callable(getattr(handoff,key,None)):raise ValueError('FULL_SITE_SHARED_HANDOFF_REQUIRED')
    cfg=spec['consumerConfig']
    if not cfg.get('workflowWorkersHandoff',{}).get('enabled') or not cfg.get('workflowExecutionHandoff',{}).get('enabled'):
        raise ValueError('FULL_SITE_HANDOFF_NOT_CONFIGURED')
    paths=spec['roles']
    if set(paths)!={'document','image','animation','postprocess','music'}:
        raise ValueError('FULL_SITE_EXACT_FIVE_ROLES_REQUIRED')
    def build(common,bridge,model,terminal):
        domains={}
        native=native_bindings(common,bridge,model,terminal,domains)
        lives=native['lifecycles']
        if set(lives)!={'document','image','pose','animation','postprocess'} or any(v is None for v in lives.values()):
            raise ValueError('FULL_SITE_NATIVE_LIFECYCLES_REQUIRED')
        if not callable(native.get('install')) or native.get('completion_loop') is None:
            raise ValueError('FULL_SITE_NATIVE_BOOTSTRAP_REQUIRED')
        def member(row):
            # Always reload the Common authority; batch rows have id, not request_id.
            rid=row.get('request_id') or row.get('id')
            return handoff.workflow_member_owned(common.db,common.row(rid),cfg) is True
        def outer(row):
            return handoff.workflow_execution_owned(common.db,common.row(row['request_id']),cfg) is True
        def frame(row):return member(row) or handoff.standalone_frame(common.db,common.row(row['request_id'])) is True
        pds=spec['pdsUrl'];doc=paths['document'];image=paths['image'];anim=paths['animation'];frames=paths['postprocess'];music=paths['music']
        factories={
          'document':document_domain_factory(sources,scratch_root=doc['scratchRoot'],preflight=preflight,admitted_parent=member,ownership_lifecycle=lives['document']),
          'image':combined_image_domain_factory(
             portrait_pair_domain_factory(executor_root=image['executorRoot'],pds_url=pds,pds_token=spec['portraitServiceToken'],scratch_root=image['scratchRoot'],journal_root=image['journalRoot'],ownership_root=image['ownershipRoot'],admitted_request=member,preflight=preflight,cleanup=cleanup,ownership_lifecycle=lives['image'],pds_pair_reader=native.get('portrait_pair_pds_reader'),direct_pds=image.get('directPairPds',False)),
             pose_direction_domain_factory(pds_url=pds,scratch_root=image['poseScratchRoot'],journal_root=image['poseJournalRoot'],ownership_root=image['poseOwnershipRoot'],workflow_root=image['poseWorkflowRoot'],admitted_request=member,preflight=preflight,cleanup=cleanup,ownership_lifecycle=lives['pose'],pds_pose_reader=native.get('pose_direction_pds_reader'),direct_pds=image.get('directPosePds',False))),
          'animation':animation_frame_domain_factory(sources,pds_url=pds,animation_ownership_root=anim['ownershipRoot'],animation_scratch_root=anim['scratchRoot'],admitted_request=member,preflight=preflight,enabled_roles=('animation',),ownership_lifecycle=lives['animation']),
          'postprocess':postprocess_domain_factory(sources,pds_url=pds,frame_ownership_root=frames['ownershipRoot'],frame_scratch_root=frames['scratchRoot'],admitted_request=frame,preflight=preflight,ownership_lifecycle=lives['postprocess']),
          'music':image_music_domain_factory(pds_url=spec['mediaPdsUrl'],pds_token=spec['mediaToken'],music_scratch_root=music['scratchRoot'],admitted_request=music_admitted,preflight=preflight,cleanup=cleanup),
        }
        caps={'document':('document.edit',),'image':('image.generate',),'animation':('animation.generate',),'postprocess':('frames.extract',),'music':('music.generate',)}
        def capture(name):
            def factory(*args):
                value=factories[name](*args);domains[name]=value;return value
            return factory
        # Failure routing must preserve unsupported states rather than invent release.
        def failure_guard(rid):return native['failure_guard_reader'](rid)
        value=composite_domain_factory([(name,caps[name],capture(name)) for name in caps],failure_guard_reader=failure_guard)(common,bridge,model,terminal)
        for key in ('failure_operation_scope','failure_publication_reader'):
            if not callable(native.get(key)):raise ValueError('FULL_SITE_NATIVE_FAILURE_PORT_REQUIRED')
            value[key]=native[key]
        def portrait_service(rid):
            return sources.modules['parent_host'].build(common,'portrait',external_children=True)
        def owns_portrait(row):
            if not member(row):return False
            selection=json.loads(row['selection_json']);stages=selection.get('stages',[])
            return len(stages)==1 and stages[0].get('id')=='portrait' and stages[0].get('candidateCount')==1
        portrait_loop=ExternalParentRunner(common,portrait_service,owns_parent=owns_portrait)
        def pose_service(rid):
            workflow=sources.modules['pose_worker'].PoseWorkflow(image['poseWorkflowRoot'])
            workflow.verify_child_publication=domains['image']['verify_pose_child_publication']
            return sources.modules['pose_service'].PoseRequests(common,image['poseServiceRoot'],workflow,external_children=True)
        value['parent_loop']=LoopGroup([('domain-parents',value['parent_loop']),('portrait',portrait_loop)])
        value['pose_batch_loop']=PoseBatchLoop(common,pose_service,member)
        value['frame_no_post_snapshot']=domains['postprocess'].get('frame_no_post_snapshot')
        value['media_ownership_loop']=LoopGroup([(key,loop) for key,loop in [('domain-ownership',value.get('media_ownership_loop')),('native',native['completion_loop'])] if loop is not None])
        value['workflow_gateway']=gateway_from_pinned_sources(sources,common,RLock(),one_stop_root=spec['oneStopRoot'],executor_id=spec['workflowExecutor'],owns_parent=outer)
        value['workflow_retry_loop']=retry_loop_from_pinned_sources(sources,common,one_stop_root=spec['oneStopRoot'],owns_parent=outer)
        value['settled_pose_lifecycle']=lives['pose']
        value['unpublished_animation_lifecycle']=lives['animation'].native
        value['startup']=native['install']
        value['site_domains']=domains
        return value
    return build
