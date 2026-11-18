"""Explicit Request-service loop wiring, with service-only recovery ports.

Creation is inert. Schema initialization and start are explicit deployment steps.
Thread-safe authority connections/transactions must be supplied by the host.
"""
from ..adapters.reconciliation import ReconciliationSupervisor
from ..adapters.document_auto_apply import ParentAutoApply
from .lifecycle import RequestBackgroundLoops


def build_request_loops(*,bridge,document_bridge,service_for_parent,
                        inspect_publication,verify_recovery_publication,cleanup,
                        model_authority=None,resume_publication=None,on_error=None):
    # Media callbacks must load durable original attempt/PDS evidence, not renew
    # an expired worker lease or manufacture a new source ownership guard.
    ports=(service_for_parent,inspect_publication,verify_recovery_publication,cleanup)
    if any(not callable(p) for p in ports):raise ValueError('REQUEST_RECOVERY_PORT_REQUIRED')
    def document(claim):return claim.capability in ('document.edit','document.create')
    def inspect(claim):
        return document_bridge.inspect_recovery(claim) if document(claim) else inspect_publication(claim)
    def verify(claim,receipt):
        if document(claim):return document_bridge.verify_recovery_publication(claim,receipt)
        return verify_recovery_publication(claim,receipt)
    supervisor=ReconciliationSupervisor(bridge,inspect,cleanup,model_authority=model_authority,
                                      verify_recovery_publication=verify,resume_publication=resume_publication)
    parent=ParentAutoApply(bridge.common,service_for_parent,on_error=on_error)
    return RequestBackgroundLoops(reconciliation=supervisor,parent_auto_apply=parent)
