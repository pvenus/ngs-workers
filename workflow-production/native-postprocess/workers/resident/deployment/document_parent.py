"""Wire reviewed existing NGS domain types on the Request service only."""
from ..adapters.document_parent_bridge import ParentDraftBridge
from ..adapters.document_session import ParentSessionResolver,ownership_scope
from ..adapters.ownership_guard import PinnedOwnershipGuardResolver
from .domain_resolvers import validate_document_envelope


def build_parent_bridge(*,common,request_authority,executor,parent_service,
                        session_type,ownership_module,category_dispatch,guard_wrapper=None):
    holder={}
    def authorize(claim):
        validate_document_envelope(claim.parameters)
        request_authority.authorize_publication(claim)
        return True
    def scope(claim):return holder['scope'](claim)
    def mark_publication(parent_id):
        failure=getattr(request_authority,'failure_fence',None)
        if failure is None:raise ValueError('DOCUMENT_FAILURE_FENCE_REQUIRED')
        failure.mark_parent_publication_started(parent_id)
    parent=ParentDraftBridge(common,executor,authorize,category_dispatch,scope,publication_started=mark_publication)
    resolver=PinnedOwnershipGuardResolver(ParentSessionResolver(parent,parent_service,session_type))
    if guard_wrapper is not None:resolver=guard_wrapper(resolver)
    holder['scope']=ownership_scope(resolver,ownership_module)
    return parent,resolver
