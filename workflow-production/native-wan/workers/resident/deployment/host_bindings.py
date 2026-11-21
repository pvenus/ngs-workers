"""Compose real service authorities from reviewed site ports, never fake defaults.

Site ports supply original domain factories and PDS proof readers. This is not a
site deployment until those ports and the existing-source DB selection are bound.
"""
from threading import RLock
from .request_host import HostPorts
from .thread_authority import ThreadAuthority,ScopedLoop
from .request_service import build_handler
from .lifecycle import RequestBackgroundLoops
from ..adapters.common_authority import CommonAuthorityBridge
from ..adapters.model_residency import ModelResidencyAuthority
from ..adapters.trusted_process_evidence import TrustedProcessEvidence
from ..adapters.reconciliation import ReconciliationSupervisor


def build_host(*,authority_factory,domain_factory,settings):
 if not callable(authority_factory)or not callable(domain_factory):raise ValueError('REQUEST_SITE_FACTORIES_REQUIRED')
 common=ThreadAuthority(authority_factory);lock=RLock();holder={}
 bridge=CommonAuthorityBridge(common,lock,lambda profile:holder['domain']['selector'](profile),
  lambda c,r:holder['domain']['verify_publication'](c,r),settings['capacities'],
  verify_stopped=lambda c:holder['terminal'].verify_stopped(c) or (holder.get('retained') is not None and holder['retained'].verify_idle(c)) or (holder.get('host_recovery') is not None and holder['host_recovery'].verify_stopped(c)),
  guard_resolver=lambda c:holder['domain']['guard_resolver'](c))
 model=ModelResidencyAuthority(common,lock,settings['hostBudgets'],settings['modelProfiles'],
  verify_unloaded=lambda lease:holder['terminal'].verify_unloaded(lease))
 terminal=TrustedProcessEvidence(bridge,settings['terminalProfiles'],model_authority=model)
 holder['terminal']=terminal
 retained=None
 if settings.get('enableRetainedAnimation')is True:
  from ..adapters.animation_retained import RetainedAnimationAuthority
  retained=RetainedAnimationAuthority(bridge,model,trusted_worker=lambda worker:holder['domain']['trusted_retained_worker'](worker))
 holder['retained']=retained
 host_recovery=None;host_recovery_token=None
 if settings.get('hostRecovery') is not None:
  from .host_recovery_site import configured
  host_recovery,host_recovery_token=configured(bridge,settings['hostRecovery'])
  for key in ('identities','modelIdentities','workflowIdentities','publicationServiceTokens'):
   if host_recovery_token in (settings.get(key) or {}):raise ValueError('HOST_RECOVERY_TOKEN_REUSED')
 liveness=None
 if settings.get('attemptLiveness') is not None:
  from ..adapters.attempt_liveness import AttemptLiveness
  config=settings['attemptLiveness']
  if set(config)!={'schemaVersion','enabled','intervalSeconds','workerManagedResources'} or config['schemaVersion']!='resident.attempt-liveness.v1' or config['enabled']is not True or not set(config['workerManagedResources'])<={'codex','image-api','cpu-image','video-global','music-model'}:raise ValueError('ATTEMPT_LIVENESS_CONFIG')
  liveness=AttemptLiveness(bridge,interval=config['intervalSeconds']);bridge.attempt_liveness=liveness
  bridge.worker_managed_resources=frozenset(config['workerManagedResources'])
 holder['host_recovery']=host_recovery
 if host_recovery is not None:bridge.host_recovery_ack=host_recovery.acknowledge_finalized
 # Domain factory creates callback adapters only. Any DB-bound service is made
 # inside its callback's current ThreadAuthority scope, not captured on this thread.
 domain=domain_factory(common,bridge,model,terminal);holder['domain']=domain
 required=('selector','verify_publication','guard_resolver','inspect_publication',
           'verify_recovery_publication','cleanup','preflight','initialize_schema','failure_guard_reader')
 if not isinstance(domain,dict)or any(not callable(domain.get(k))for k in required):raise ValueError('REQUEST_SITE_DOMAIN_PORTS_REQUIRED')
 if retained is not None and not callable(domain.get('trusted_retained_worker')):raise ValueError('RETAINED_SOURCE_VERIFIER_REQUIRED')
 timeout_disposition=model_timeout=None
 if domain.get('animation_timeout_observer') is not None:
  if liveness is None or not callable(domain['animation_timeout_observer']):raise ValueError('ANIMATION_TIMEOUT_DISPOSITION_PORTS')
  from ..adapters.animation_timeout_disposition import AnimationTimeoutDisposition,ReleasedAnimationModels
  import time
  timeout_disposition=AnimationTimeoutDisposition(bridge,observe=domain['animation_timeout_observer'],model_reader=ReleasedAnimationModels(bridge,terminal),clock=time.time)
  bridge.timeout_disposition=timeout_disposition
  domain['close_animation_timeout']=timeout_disposition.close
  if domain.get('animation_model_timeout_observer') is not None:
   from ..adapters.animation_model_timeout import AnimationModelTimeout
   if not callable(domain['animation_model_timeout_observer']):raise ValueError('ANIMATION_MODEL_TIMEOUT_OBSERVER_REQUIRED')
   model_timeout=AnimationModelTimeout(timeout_disposition,model,domain['animation_model_timeout_observer'])
   timeout_disposition.model_reader.model_timeout=model_timeout
   domain['close_animation_model_timeout']=model_timeout.close
   domain['close_idle_animation_model_timeout']=model_timeout.close_idle
 from ..adapters.failure_fence import FailureFence
 failure=FailureFence(bridge,domain['failure_guard_reader'],operation_scope=domain.get('failure_operation_scope'),publication_reader=domain.get('failure_publication_reader'));bridge.failure_fence=failure
 if domain.get('frame_no_post_observer') is not None:
  from ..adapters.frame_no_post import FrameNoPostReader
  failure.frame_no_post_reader=FrameNoPostReader(bridge,domain['frame_no_post_snapshot'],domain['frame_no_post_observer'])
 if domain.get('settled_pose_lifecycle') is not None:
  from ..adapters.settled_failure_retry import SettledPoseRetry
  loop=domain.get('workflow_retry_loop')
  if loop is None:raise ValueError('SETTLED_POSE_RETRY_PORTS')
  pose_retry=SettledPoseRetry(failure,domain['settled_pose_lifecycle']).read
  animation_lifecycle=domain.get('unpublished_animation_lifecycle')
  if animation_lifecycle is not None:
   from ..adapters.unpublished_animation_retry import UnpublishedAnimationRetry
   animation_retry=UnpublishedAnimationRetry(failure,animation_lifecycle).read
   from ..adapters.settled_animation_retry import SettledAnimationRetry
   animation_settled_retry=SettledAnimationRetry(failure,animation_lifecycle).read
   def released_retry(stage,rid,actor,key=None):
    value=animation_retry(stage,rid,actor,key)
    if value is None:value=animation_settled_retry(stage,rid,actor,key)
    return value if value is not None else pose_retry(stage,rid,actor,key)
   loop.bind_settled_retry_reader(released_retry)
  else:loop.bind_settled_retry_reader(pose_retry)
 if 'parent_loop'not in domain:raise ValueError('REQUEST_EXTERNAL_PARENT_LOOP_REQUIRED')
 supervisor=ReconciliationSupervisor(bridge,domain['inspect_publication'],domain['cleanup'],
  model_authority=model,verify_recovery_publication=domain['verify_recovery_publication'],
  resume_publication=domain.get('resume_publication'))
 logical=None;logical_config=settings.get('logicalRetry')
 if logical_config is not None:
  if liveness is None or set(logical_config)!={'schemaVersion','closureToken','retryToken'}or logical_config['schemaVersion']!='resident.logical-timeout-retry.v1':raise ValueError('LOGICAL_RETRY_CONFIG')
  for token in (logical_config['closureToken'],logical_config['retryToken']):
   if token==host_recovery_token or any(token in (settings.get(k)or {}) for k in ('identities','modelIdentities','workflowIdentities','publicationServiceTokens')):raise ValueError('LOGICAL_RETRY_TOKEN_REUSED')
  from ..adapters.logical_retry import LogicalRetryAuthority
  logical=LogicalRetryAuthority(bridge,parent_reader=domain.get('logical_parent_reader'),revoke=domain.get('revoke_native_publication'),read_revocation=domain.get('read_native_revocation'))
 unpublished=None
 if settings.get('unpublishedImageRetry') is not None:
  if settings['unpublishedImageRetry']!='request-owned-image.v1'or liveness is None or logical is not None:raise ValueError('UNPUBLISHED_IMAGE_RETRY_CONFIG')
  from ..adapters.unpublished_image_retry import UnpublishedImageRetry
  service=domain.get('portrait_pair_service');loop=domain.get('workflow_retry_loop')
  if service is None or loop is None:raise ValueError('UNPUBLISHED_IMAGE_RETRY_PORTS')
  unpublished=UnpublishedImageRetry(bridge,parent_reader=domain.get('logical_parent_reader'),record_reader=service.record)
  loop.bind_logical_retry_reader(unpublished.read)
 additional=[]
 for key in ('media_ownership_loop','animation_batch_loop','pose_batch_loop','workflow_retry_loop','native_png_loop'):
  if domain.get(key)is not None:additional.append((key,ScopedLoop(domain[key],common)))
 if liveness is not None:additional.append(('attempt-liveness',ScopedLoop(liveness,common)))
 loops=RequestBackgroundLoops(reconciliation=ScopedLoop(supervisor,common),parent_auto_apply=ScopedLoop(domain['parent_loop'],common)if domain['parent_loop']is not None else None,additional_loops=additional)
 handler=build_handler(bridge,settings['identities'],document_bridge=domain.get('document_bridge'),model_authority=model,terminal_authority=terminal,retained_authority=retained,publication_tokens=settings.get('publicationServiceTokens'),model_identities=settings.get('modelIdentities'),portrait_pair_service=domain.get('portrait_pair_service'),workflow_gateway=domain.get('workflow_gateway'),workflow_identities=settings.get('workflowIdentities'),pose_direction_service=domain.get('pose_direction_service'),media_source=domain.get('media_source'),owned_operation_authority=domain.get('owned_operation_authority'))
 if host_recovery is not None:
  from ..adapters.host_recovery_http import handler as recovery_handler,PREFIX as recovery_prefix
  recovery=recovery_handler(host_recovery,host_recovery_token)
  original=handler
  class CombinedHandler(original):
   def do_POST(self):
    if self.path.startswith(recovery_prefix):return recovery.do_POST(self)
    return super().do_POST()
  handler=CombinedHandler
 if logical is not None:
  from ..adapters.logical_retry_http import handler as logical_handler
  logical_http=logical_handler(logical,closure_token=logical_config['closureToken'],retry_token=logical_config['retryToken'],parent_actor=lambda rid:common.row(rid)['requester'])
  previous=handler
  class LogicalHandler(previous):
   reply=logical_http.reply
   def do_POST(self):
    if self.path.startswith(('/v1/native-publication-closures/','/v1/logical-timeout-retry/')):return logical_http.do_POST(self)
    return super().do_POST()
  handler=LogicalHandler
 def initialize():
  bridge.initialize_schema();failure.initialize_schema();model.initialize_schema();terminal.initialize_schema();supervisor.initialize_schema();domain['initialize_schema']()
  if domain.get('workflow_gateway')is not None:domain['workflow_gateway'].initialize_schema()
  if retained is not None:retained.initialize_schema()
  if host_recovery is not None:host_recovery.initialize_schema()
  if liveness is not None:liveness.initialize_schema()
  if timeout_disposition is not None:timeout_disposition.initialize_schema()
  if model_timeout is not None:model_timeout.initialize_schema()
  if logical is not None:logical.initialize_schema()
  if unpublished is not None:unpublished.initialize_schema()
 return HostPorts(common,handler,loops,domain['preflight'],initialize,domain.get('startup'))
