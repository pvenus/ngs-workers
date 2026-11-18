"""Wire reviewed existing NGS domain types on the Request service only."""
from ..adapters.document_parent_bridge import ParentDraftBridge
from ..adapters.document_session import ParentSessionResolver,ownership_scope
from ..adapters.ownership_guard import PinnedOwnershipGuardResolver
from .domain_resolvers import validate_document_envelope


def build_parent_bridge(*,common,request_authority,executor,parent_service,
                        session_type,ownership_module,category_dispatch):
    holder={}
    def authorize(claim):
        validate_document_envelope(claim.parameters)
        request_authority.authorize_publication(claim)
        return True
    def scope(claim):return holder['scope'](claim)
    parent=ParentDraftBridge(common,executor,authorize,category_dispatch,scope)
    resolver=PinnedOwnershipGuardResolver(ParentSessionResolver(parent,parent_service,session_type))
    holder['scope']=ownership_scope(resolver,ownership_module)
    return parent,resolver
