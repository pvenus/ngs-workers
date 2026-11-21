"""One independent resident process per role, pulling formal claims only."""
import logging
import re
import time
from threading import Event, Thread
from .contracts import Backend, PDS, Profile, Requests, ExecutionStopUnconfirmed

LOG = logging.getLogger(__name__)

SAFE_CODES = {'CLAIM_NOT_CURRENT', 'LEASE_LOST', 'PDS_WORKSPACE_REQUIRED',
              'NO_ARTIFACTS', 'ARTIFACT_OUTSIDE_PDS_WORKSPACE',
              'INCOMPLETE_FORMAL_CLAIM', 'UNSUPPORTED_CAPABILITY',
              'INVALID_INPUT_REFERENCE', 'RECEIPT_CLAIM_MISMATCH',
              'PUBLICATION_NOT_VERIFIED'}

# Exact codes from the native pose/PNG validators. Never persist arbitrary
# exception text, file paths, prompts, or tokens via a broad new prefix.
SAFE_POSE_FAILURE_CODES = frozenset({
    'NODE_IMAGE_ACTUAL_ARGUMENTS_MISMATCH',
    'NODE_IMAGE_ACTUAL_ARGUMENT_RECEIPT_REQUIRED',
    'NODE_IMAGE_ARGUMENT_FILE_IMMUTABLE',
    'NODE_IMAGE_EXACTLY_ONE_NATIVE_IMAGE_REQUIRED',
    'NODE_IMAGE_GENERATION_UNCONFIRMED_RECOVER_ONLY',
    'NODE_IMAGE_NATIVE_ALREADY_STARTED_RECOVER_ONLY',
    'NODE_IMAGE_NATIVE_BLOCKED_RECOVER_ONLY',
    'NODE_IMAGE_NATIVE_OUTPUT_CALL_BINDING_INVALID',
    'NODE_IMAGE_NATIVE_OUTPUT_PATH_DENIED',
    'NODE_IMAGE_NATIVE_OUTPUT_ROOTS_REQUIRED',
    'NODE_IMAGE_NATIVE_RECOVERED_HASH_MISMATCH',
    'NODE_IMAGE_NATIVE_RECOVERY_BINDING_INVALID',
    'NODE_IMAGE_PINNED_NATIVE_DESCRIPTOR_REQUIRED',
    'NODE_IMAGE_REFERENCE_BYTES_CHANGED',
    'NODE_IMAGE_REFERENCE_MANIFEST_REQUIRED',
    'NODE_IMAGE_REFERENCE_SCOPE_DENIED',
    'NODE_IMAGE_REQUEST_SOURCE_CONFLICT',
    'NODE_IMAGE_WORKER_BUSY',
    'POSE_BINDING_CONFLICT',
    'POSE_CENTER_CHANGED',
    'POSE_CENTER_HASH',
    'POSE_CENTER_REFERENCE_REQUIRED',
    'POSE_CHILD_BINDING',
    'POSE_DIRECTION_CHANGED',
    'POSE_DIRECTION_CHILD_BINDING',
    'POSE_DIRECTION_REQUEST_REQUIRED',
    'POSE_DIRECTION_SCOPE',
    'POSE_EXISTING_DIRECTION_REQUIRED',
    'POSE_NATIVE_OUTCOME_UNKNOWN',
    'POSE_NPC_REQUIRED',
    'POSE_OUTPUT_RETRY_LIMIT',
    'POSE_PARENT_STATE_CHANGED',
    'POSE_PDS_',
    'POSE_PDS_PATH_DENIED',
    'POSE_PDS_REDIRECT_DENIED',
    'POSE_PLANNING_CHANGED',
    'POSE_PROMPT_CONTEXT_INVALID',
    'POSE_PROVIDER_CLAIM_CONFLICT',
    'POSE_PROVIDER_CLAIM_HASH_INVALID',
    'POSE_PROVIDER_PROOF_INVALID',
    'POSE_REAR_ASSET_INVALID',
    'POSE_REAR_DEPENDENCY_REQUIRED',
    'POSE_REAR_PARENT_LINEAGE_INVALID',
    'POSE_REAR_RECEIPT_INVALID',
    'POSE_REAR_REFERENCE_CHANGED',
    'POSE_REAR_SLOT_INVALID',
    'POSE_RECEIPT_UNVERIFIED',
    'POSE_REFERENCE_COUNT',
    'POSE_REFERENCE_SET_INVALID',
    'POSE_RESULT_CHANGED',
    'POSE_REVIEW_EVIDENCE_CHANGED',
    'POSE_SOURCE_HASH_INVALID',
    'TRANSPARENT_PNG_ALPHA_CHANNEL_REQUIRED',
    'TRANSPARENT_PNG_BACKGROUND_COVERAGE_REQUIRED',
    'TRANSPARENT_PNG_BACKGROUND_REQUIRED',
    'TRANSPARENT_PNG_VISIBLE_FOREGROUND_REQUIRED',
})

def error_code(exc):
    code = str(exc)
    safe_domain=re.fullmatch(r'(?:FRAME|FRAMES|ANIMATION|DOCUMENT|MUSIC|IMAGE|MODEL|PDS|PUBLICATION|FORMAL|NPC|COMMON|WORKER|RESOURCE|EXECUTION|CHECKPOINT|RECOVERY|REQUEST|SERVER|MEDIA|FULL_FRAME)_[A-Z0-9_]{1,140}',code)
    return code if code in SAFE_CODES or code in SAFE_POSE_FAILURE_CODES or safe_domain else type(exc).__name__


# Diagnostic-only: no exception text, traceback, claim or provider payload.
def _failure_diagnostic(phase, exc):
    try:
        kinds = ('KeyboardInterrupt', 'SystemExit', 'TimeoutError', 'ConnectionError',
                 'ValueError', 'RuntimeError', 'OSError', 'HTTPError',
                 'ExecutionStopUnconfirmed', 'ReconciliationRequired')
        kind = type(exc).__name__
        if kind not in kinds: kind = 'OtherBaseException' if not isinstance(exc, Exception) else 'OtherException'
        value = exc.args[0] if len(exc.args) == 1 and type(exc.args[0]) is str else ''
        code = value if value in SAFE_CODES or value in SAFE_POSE_FAILURE_CODES or value in {
            'EXECUTION_EVIDENCE_REQUIRED', 'EXECUTION_STOP_UNCONFIRMED',
            'MODEL_UNLOAD_EVIDENCE_UNCONFIRMED', 'PUBLICATION_OR_ACK_UNCERTAIN',
        } or re.fullmatch(r'REQUEST_API_HTTP_[1-5][0-9]{2}', value) else 'UNCLASSIFIED'
        LOG.warning('RESIDENT_FAILURE phase=%s exception=%s code=%s', phase, kind, code)
    except BaseException:
        pass  # A broken log sink must not affect lifecycle decisions.



class ReconciliationRequired(RuntimeError):
    """Do not re-execute after an ambiguous publication/acknowledgment."""


class ResidentWorker:
    def __init__(self, profile: Profile, worker_id: str, requests: Requests,
                 pds: PDS, backend: Backend, *, idle_seconds: float = 60,
                 heartbeat_seconds: float = 10, clock=time.monotonic, residency=None, evidence=None):
        if not worker_id or idle_seconds < 0 or heartbeat_seconds <= 0:
            raise ValueError('INVALID_WORKER_CONFIG')
        self.profile, self.worker_id = profile, worker_id
        self.requests, self.pds, self.backend = requests, pds, backend
        self.idle_seconds, self.heartbeat_seconds = idle_seconds, heartbeat_seconds
        self.clock, self.last_used, self.loaded = clock, clock(), False
        self.stop = Event()
        self.residency, self.model_lease = residency, None
        self.model_done, self.model_lost = Event(), Event()
        self.model_thread = None
        self.active_cancel = None
        self.shutdown_unconfirmed = False
        self.evidence = evidence

    def _record_execution(self, claim, *, entered):
        if self.evidence is None:return
        hook=getattr(self.backend,'execution_evidence',None)
        proof=hook(claim) if entered and callable(hook) else None
        if not entered:
            proof={'outcome':'never_started','processGroups':[],'returnCodes':[]}
        if proof is None:raise ExecutionStopUnconfirmed('EXECUTION_EVIDENCE_REQUIRED')
        try:self.evidence.record_execution(claim,self.worker_id,proof)
        except Exception as exc:
            raise ExecutionStopUnconfirmed('EXECUTION_EVIDENCE_UNCONFIRMED') from exc

    def _unload(self):
        if self.shutdown_unconfirmed:
            return  # Only explicit supervisor reconciliation may recover this session.
        if self.loaded:
            try:self.backend.unload()
            except Exception as exc:
                self.shutdown_unconfirmed=True;self.stop.set()
                if isinstance(exc,ExecutionStopUnconfirmed):raise
                raise ExecutionStopUnconfirmed('MODEL_UNLOAD_UNCONFIRMED') from exc
            self.loaded = False
        if self.model_lease is not None:
            self.model_done.set()
            if self.model_thread is not None:self.model_thread.join()
            if self.evidence is not None:
                hook=getattr(self.backend,'model_unload_evidence',None)
                proof=hook(self.model_lease) if callable(hook) else None
                try:
                    if proof is None:raise ValueError('MODEL_UNLOAD_EVIDENCE_REQUIRED')
                    self.evidence.record_model(self.model_lease,self.worker_id,proof)
                except Exception as exc:
                    self.shutdown_unconfirmed=True;self.stop.set()
                    raise ExecutionStopUnconfirmed('MODEL_UNLOAD_EVIDENCE_UNCONFIRMED') from exc
            self.residency.release(self.model_lease)
            self.model_lease = None
            self.model_thread = None
            self.backend.set_model_lease(None)

    def _load(self, cancelled):
        if self.residency is not None:
            if not hasattr(self.backend,'set_model_lease'):
                raise ValueError('MODEL_LEASE_BACKEND_REQUIRED')
            while self.model_lease is None:
                if cancelled.is_set() or self.stop.is_set():raise RuntimeError('LEASE_LOST')
                self.model_lease = self.residency.acquire(self.profile,self.worker_id)
                if self.model_lease is None:cancelled.wait(self.heartbeat_seconds)
            self.model_done.clear();self.model_lost.clear()
            self.backend.set_model_lease(self.model_lease)
            def heartbeat_model():
                while not self.model_done.wait(self.heartbeat_seconds):
                    try:healthy=self.residency.renew(self.model_lease)
                    except Exception:healthy=False
                    if not healthy:
                        self.model_lost.set();self.stop.set()
                        if self.active_cancel is not None:self.active_cancel.set()
                        return
            self.model_thread=Thread(target=heartbeat_model,name=self.profile.role+'-model-lease',daemon=True)
            self.model_thread.start()
            if not self.residency.renew(self.model_lease):
                self.model_lost.set();raise RuntimeError('LEASE_LOST')
        # Mark loading before entering backend: partial loads also need unload.
        self.loaded = True
        self.backend.load()

    def _renew_phase(self, claim, phase):
        try:return self.requests.renew(claim)
        except TimeoutError:
            if self.profile.role != 'animation':raise
            raise RuntimeError('ANIMATION_'+phase+'_RENEW_TIMEOUT') from None

    def poll_once(self) -> bool:
        if self.stop.is_set():
            if self.model_lost.is_set():self._unload()
            return False
        claim = self.requests.claim(self.profile, self.worker_id)
        if claim is None:
            if self.clock() - self.last_used >= self.idle_seconds:
                self._unload()
            return False
        workspace = None
        publish_started = False
        stage_started = False
        release_allowed = True
        execution_entered = execution_recorded = False
        done, cancelled = Event(), Event()
        self.active_cancel = cancelled
        def heartbeat():
            while not done.wait(self.heartbeat_seconds):
                try:
                    healthy = self.requests.renew(claim)
                except Exception:
                    healthy = False
                if not healthy:
                    cancelled.set()
                    return
        thread = None
        phase = 'validate'
        try:
            claim.validate(self.profile)
            # Check authoritative execution ownership before fetching inputs.
            phase = 'pre_stage_renew'
            if not self._renew_phase(claim, "PRE_STAGE"):
                raise RuntimeError('CLAIM_NOT_CURRENT')
            thread = Thread(target=heartbeat, name=f'{self.profile.role}-lease', daemon=True)
            thread.start()
            # Optional trusted composition dispatch, never request-provided code.
            # Unload the previous backend under its original evidence before
            # selecting another implementation for this claim.
            select_backend=getattr(self.backend,'select_claim',None)
            if callable(select_backend):
                if self.loaded and self.backend.needs_switch(claim):self._unload()
                select_backend(claim)
            stage_started = True
            phase = 'stage'
            workspace = self.pds.stage(claim)
            if not workspace.is_dir():
                raise RuntimeError('PDS_WORKSPACE_REQUIRED')
            if cancelled.is_set():
                raise RuntimeError('LEASE_LOST')
            needs=getattr(self.backend,'requires_model_for',None)
            model_needed=self.profile.model or (bool(needs(claim)) if callable(needs) else bool(getattr(self.backend,'requires_model',False)))
            if model_needed and not self.loaded:
                phase = 'model_load'
                self._load(cancelled)
            # A model load may outlive the request/model keepalive. Never start
            # generation after cancellation, even if load itself returned cleanly.
            if cancelled.is_set() or self.model_lost.is_set() or self.stop.is_set():
                raise RuntimeError('LEASE_LOST')
            phase = 'pre_execution_renew'
            if not self._renew_phase(claim, "PRE_EXECUTION"):
                cancelled.set()
                raise RuntimeError('LEASE_LOST')
            execution_entered = True
            phase = 'execute'
            artifacts = self.backend.execute(claim, workspace, cancelled)
            phase = 'execution_evidence'
            self._record_execution(claim,entered=True)
            execution_recorded = True
            phase = 'post_execution_unload'
            if getattr(self.backend,'release_model_after_execute',False):self._unload()
            phase = 'post_execution_renew'
            if cancelled.is_set() or self.model_lost.is_set() or not self._renew_phase(claim, "POST_EXECUTION"):
                raise RuntimeError('LEASE_LOST')
            phase = 'artifact_validation'
            if not artifacts:
                raise RuntimeError('NO_ARTIFACTS')
            root = workspace.resolve(strict=True)
            for item in artifacts:
                target = item.resolve(strict=True)
                if root not in target.parents or not target.is_file():
                    raise RuntimeError('ARTIFACT_OUTSIDE_PDS_WORKSPACE')
            phase = 'publish'
            publish_started = True
            if self.evidence is not None:self.requests.mark_publication_started(claim)
            receipt = self.pds.publish(claim, workspace, artifacts)
            receipt.validate(claim)
            # Durable service-side publication proof must survive deletion of scratch.
            phase = 'checkpoint'
            self.requests.checkpoint(claim,receipt)
            phase = 'cleanup'
            try:
                cleanup_done = self.pds.cleanup(claim, workspace)
            except Exception:
                cleanup_done = False
            # Server repeats fence/receipt checks; cleanup_pending is explicit.
            phase = 'complete'
            self.requests.complete(claim, receipt, cleanup_done)
            return True
        except Exception as exc:
            _failure_diagnostic(phase, exc)
            if isinstance(exc,ExecutionStopUnconfirmed):
                release_allowed=False
                self.shutdown_unconfirmed=True
                self.stop.set()
                self.requests.execution_unknown(claim,'EXECUTION_STOP_UNCONFIRMED')
                raise
            if publish_started or isinstance(exc,ReconciliationRequired):
                self.stop.set()
                # Preserve ambiguous output for official reconciliation, do not
                # re-run generation or delete potentially needed temp files.
                self.requests.uncertain(claim, error_code(exc))
                raise ReconciliationRequired('PUBLICATION_OR_ACK_UNCERTAIN') from exc
            if self.residency is not None and (self.loaded or self.model_lease is not None):
                try:self._unload()
                except Exception:
                    release_allowed=False;self.shutdown_unconfirmed=True;self.stop.set()
                    self.requests.execution_unknown(claim,'EXECUTION_STOP_UNCONFIRMED')
                    raise
            if not execution_recorded:
                try:self._record_execution(claim,entered=execution_entered)
                except ExecutionStopUnconfirmed:
                    release_allowed=False;self.shutdown_unconfirmed=True;self.stop.set()
                    self.requests.execution_unknown(claim,'EXECUTION_STOP_UNCONFIRMED')
                    raise
            cleanup_done = not stage_started
            if stage_started:
                try:
                    cleanup_done = self.pds.cleanup(claim, workspace)
                except Exception:
                    cleanup_done = False
            self.requests.fail(claim, error_code(exc), cleanup_done)
            return True
        except BaseException as exc:
            _failure_diagnostic(phase, exc)
            raise
        finally:
            done.set()
            if thread is not None:
                thread.join()
            self.active_cancel = None
            self.last_used = self.clock()
            # execute() must join its children before returning or raising.
            if release_allowed:
                try:
                    self.requests.release_resource(claim)
                except BaseException as exc:
                    _failure_diagnostic('release_resource', exc)
                    raise

    def shutdown(self):
        self.stop.set()
        self._unload()

    def serve(self, poll_seconds: float = 1):
        if poll_seconds <= 0:
            raise ValueError('INVALID_POLL_INTERVAL')
        try:
            while not self.stop.is_set():
                self.poll_once()
                self.stop.wait(poll_seconds)
        finally:
            self.shutdown()
