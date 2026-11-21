"""Explicit host composition only; default role factory/selection is unchanged.

PDS source preparation happens before Common's claim transaction, not in its
selection callback. Thread-local preparation is disposable; the exact Common
snapshot is rechecked by the existing native selector. No new lease or token.
"""
from threading import local
from contextlib import contextmanager
from ..adapters.common_authority import CommonAuthorityBridge

class NativePNGRequests:
    def __init__(self,workflow,lock,publication_proof,capacities,*,verify_frozen,verify_stopped,guard_resolver=None):
        if not callable(verify_frozen) or not callable(verify_stopped):
            raise ValueError('WORKER_TRUSTED_BINDING_REQUIRED')
        self.workflow=workflow;self.verify_frozen=verify_frozen;self.prepared=local()
        def select(profile):
            if profile.role=='animation':
                return workflow.select_prepared_frozen_resident(profile.role,getattr(self.prepared,'value',None))
            return workflow.select_for_resident(profile.role)
        self.authority=CommonAuthorityBridge(workflow.common,lock,select,publication_proof,capacities,
                                            verify_stopped=verify_stopped,guard_resolver=guard_resolver,native_png=True)
        self.authority.claim_preparation=self.prepare_claim

    @contextmanager
    def prepare_claim(self,profile):
        self.prepared.value=None
        try:
            if profile.role=='animation':
                self.prepared.value=self.workflow.prepare_frozen_resident(profile.role,verify_frozen=self.verify_frozen)
            yield
        finally:
            self.prepared.value=None

    def __getattr__(self,name):
        # Preserve the actual Request authority lifecycle methods; this adapter
        # adds no replacement fail, renew, checkpoint, or release semantics.
        return getattr(self.authority,name)
