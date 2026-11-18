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

def error_code(exc):
    code = str(exc)
    safe_domain=re.fullmatch(r'(?:FRAME|FRAMES|ANIMATION|DOCUMENT|MUSIC|IMAGE|MODEL|PDS|PUBLICATION|FORMAL|NPC|COMMON|WORKER|RESOURCE|EXECUTION|CHECKPOINT|RECOVERY|REQUEST|SERVER|MEDIA|FULL_FRAME)_[A-Z0-9_]{1,140}',code)
    return code if code in SAFE_CODES or safe_domain else type(exc).__name__



class ReconciliationRequired(RuntimeError):
    """Do not re-execute after an ambiguous publication/acknowledgment."""


class ResidentWorker:
    def __init__(self, profile: Profile, worker_id: str, requests: Requests,
                 pds: PDS, backend: Backend, *, idle_seconds: float = 60,
                 heartbeat_seconds: float = 10, clock=time.monotonic, residency=None):
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
        try:
            claim.validate(self.profile)
            # Check authoritative execution ownership before fetching inputs.
            if not self.requests.renew(claim):
                raise RuntimeError('CLAIM_NOT_CURRENT')
            thread = Thread(target=heartbeat, name=f'{self.profile.role}-lease', daemon=True)
            thread.start()
            stage_started = True
            workspace = self.pds.stage(claim)
            if not workspace.is_dir():
                raise RuntimeError('PDS_WORKSPACE_REQUIRED')
            if cancelled.is_set():
                raise RuntimeError('LEASE_LOST')
            needs=getattr(self.backend,'requires_model_for',None)
            model_needed=self.profile.model or (bool(needs(claim)) if callable(needs) else bool(getattr(self.backend,'requires_model',False)))
            if model_needed and not self.loaded:
                self._load(cancelled)
            artifacts = self.backend.execute(claim, workspace, cancelled)
            if cancelled.is_set() or self.model_lost.is_set() or not self.requests.renew(claim):
                raise RuntimeError('LEASE_LOST')
            if not artifacts:
                raise RuntimeError('NO_ARTIFACTS')
            root = workspace.resolve(strict=True)
            for item in artifacts:
                target = item.resolve(strict=True)
                if root not in target.parents or not target.is_file():
                    raise RuntimeError('ARTIFACT_OUTSIDE_PDS_WORKSPACE')
            publish_started = True
            receipt = self.pds.publish(claim, workspace, artifacts)
            receipt.validate(claim)
            # Durable service-side publication proof must survive deletion of scratch.
            self.requests.checkpoint(claim,receipt)
            try:
                cleanup_done = self.pds.cleanup(claim, workspace)
            except Exception:
                cleanup_done = False
            # Server repeats fence/receipt checks; cleanup_pending is explicit.
            self.requests.complete(claim, receipt, cleanup_done)
            return True
        except Exception as exc:
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
            cleanup_done = not stage_started
            if stage_started:
                try:
                    cleanup_done = self.pds.cleanup(claim, workspace)
                except Exception:
                    cleanup_done = False
            self.requests.fail(claim, error_code(exc), cleanup_done)
            return True
        finally:
            done.set()
            if thread is not None:
                thread.join()
            self.active_cancel = None
            self.last_used = self.clock()
            # execute() must join its children before returning or raising.
            if release_allowed:
                self.requests.release_resource(claim)

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
