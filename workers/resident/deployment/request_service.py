"""Request-service HTTP composition. Never used by worker processes."""
from ..adapters.request_api import handler as request_handler
from ..adapters.document_draft_http import handler as draft_handler
from ..adapters.model_residency_http import handler as model_handler


def build_handler(bridge,identities,*,document_bridge=None,model_authority=None):
    requests=request_handler(bridge,identities)
    drafts=models=None
    if document_bridge is not None:
        def verify_worker(claim,worker):
            with bridge.transaction():return bridge._lease(claim)['workerId']==worker
        tokens={token:worker for token,(worker,role)in identities.items()if role=='document'}
        drafts=draft_handler(document_bridge,tokens,verify_worker)
    if model_authority is not None:
        from ..contracts import PROFILES
        tokens={token:(worker,role)for token,(worker,role)in identities.items()
                if role in model_authority.models}
        models=model_handler(model_authority,tokens)
    class Combined(requests):
        def do_POST(self):
            if self.path.startswith('/v1/document-drafts/'):
                if drafts is None:return self.reply(404,{'error':'DOCUMENT_PARENT_BRIDGE_REQUIRED'})
                return drafts.do_POST(self)
            if self.path.startswith('/v1/model-residency/'):
                if models is None:return self.reply(404,{'error':'MODEL_AUTHORITY_REQUIRED'})
                return models.do_POST(self)
            return requests.do_POST(self)
    return Combined
