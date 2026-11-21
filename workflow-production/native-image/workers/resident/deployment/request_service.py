"""Request-service HTTP composition. Never used by worker processes."""
from ..adapters.request_api import handler as request_handler
from ..adapters.document_draft_http import handler as draft_handler
from ..adapters.model_residency_http import handler as model_handler


def build_handler(bridge,identities,*,document_bridge=None,model_authority=None,terminal_authority=None,retained_authority=None,publication_tokens=None,model_identities=None,portrait_pair_service=None,workflow_gateway=None,workflow_identities=None,pose_direction_service=None,media_source=None):
    publication_tokens=tuple(publication_tokens or ())
    if any(token in identities or token in (model_identities or {}) for token in publication_tokens):raise ValueError('PUBLICATION_SERVICE_TOKEN_MUST_BE_SEPARATE')
    publications=None
    if publication_tokens:
        from ..adapters.publication_authority import handler as publication_handler
        publications=publication_handler(bridge,publication_tokens)
    workflows=None
    if workflow_gateway is not None:
        from ..adapters.workflow_gateway_http import handler as workflow_handler
        workflow_identities=dict(workflow_identities or {})
        if not workflow_identities or any(token in identities or token in (model_identities or {}) or token in publication_tokens for token in workflow_identities):raise ValueError('WORKFLOW_SERVICE_TOKEN_MUST_BE_SEPARATE')
        workflows=workflow_handler(workflow_gateway,workflow_identities)
    media=None
    if media_source is not None:
        from ..adapters.media_source_http import handler as media_handler
        media=media_handler(bridge,identities,media_source)
    requests=request_handler(bridge,identities)
    drafts=models=None
    pairs=None;pair_reservations=None
    if portrait_pair_service is not None:
        from ..adapters.portrait_pair_http import handler as pair_handler
        def verify_image_worker(claim,worker):
            with bridge.transaction():return bridge._lease(claim)['workerId']==worker
        tokens={token:worker for token,(worker,role)in identities.items()if role=='image'}
        pairs=pair_handler(portrait_pair_service,tokens,verify_image_worker)
        if callable(getattr(portrait_pair_service,'read_reservation',None))and publication_tokens:
            from ..adapters.portrait_pair_reservation_http import handler as pair_reservation_handler
            pair_reservations=pair_reservation_handler(portrait_pair_service,publication_tokens)
    poses=None;pose_reservations=None
    if pose_direction_service is not None:
        from ..adapters.pose_direction_http import handler as pose_handler
        def verify_pose_worker(claim,worker):
            with bridge.transaction():return bridge._lease(claim)['workerId']==worker
        tokens={token:worker for token,(worker,role)in identities.items()if role=='image'}
        poses=pose_handler(pose_direction_service,tokens,verify_pose_worker)
        if callable(getattr(pose_direction_service,'read_reservation',None))and publication_tokens:
            from ..adapters.portrait_pair_reservation_http import handler as reservation_handler
            pose_reservations=reservation_handler(pose_direction_service,publication_tokens,path='/v1/npc-pose-direction-reservations/read')
    terminals=None
    retained=None
    if retained_authority is not None:
        from ..adapters.animation_retained_http import handler as retained_handler
        retained=retained_handler(retained_authority,identities)
    if terminal_authority is not None:
        from ..adapters.terminal_evidence_http import handler as terminal_handler
        terminals=terminal_handler(terminal_authority,identities)
    if document_bridge is not None:
        def verify_worker(claim,worker):
            with bridge.transaction():return bridge._lease(claim)['workerId']==worker
        tokens={token:worker for token,(worker,role)in identities.items()if role=='document'}
        drafts=draft_handler(document_bridge,tokens,verify_worker)
    if model_authority is not None:
        from ..contracts import PROFILES
        tokens={token:(worker,role)for token,(worker,role)in (model_identities if model_identities is not None else identities).items()
                if role in model_authority.models}
        models=model_handler(model_authority,tokens)
    class Combined(requests):
        def do_GET(self):
            if (self.client_address[0]!='127.0.0.1' or self.headers.get('Origin') is not None
                    or self.headers.get('Host')!='127.0.0.1:'+str(self.server.server_port)):
                return self.reply(403,{'error':'LOOPBACK_ONLY'})
            if self.path!='/health':return self.reply(404,{'error':'NOT_FOUND'})
            return self.reply(200,{'ok':True,'service':'resident-request','apiVersion':'v1'})
        def do_POST(self):
            if self.path.startswith('/v1/media-source/'):
                if media is None:return self.reply(404,{'error':'MEDIA_SOURCE_REQUIRED'})
                return media.do_POST(self)
            if self.path.startswith('/v1/npc-pose-direction-reservations/'):
                if pose_reservations is None:return self.reply(404,{'error':'POSE_RESERVATION_READER_REQUIRED'})
                return pose_reservations.do_POST(self)
            if self.path.startswith('/v1/pose-directions/'):
                if poses is None:return self.reply(404,{'error':'POSE_DIRECTION_SERVICE_REQUIRED'})
                return poses.do_POST(self)
            if self.path.startswith('/v1/workflow-gateway/'):
                if workflows is None:return self.reply(404,{'error':'WORKFLOW_GATEWAY_REQUIRED'})
                return workflows.do_POST(self)
            if self.path.startswith('/v1/npc-portrait-pair-reservations/'):
                if pair_reservations is None:return self.reply(404,{'error':'PAIR_RESERVATION_READER_REQUIRED'})
                return pair_reservations.do_POST(self)
            if self.path.startswith('/v1/portrait-pairs/'):
                if pairs is None:return self.reply(404,{'error':'PORTRAIT_PAIR_SERVICE_REQUIRED'})
                return pairs.do_POST(self)
            if self.path.startswith('/v1/publications/'):
                if publications is None:return self.reply(404,{'error':'PUBLICATION_SERVICE_AUTH_REQUIRED'})
                return publications.do_POST(self)
            if self.path.startswith('/v1/animation-retained/'):
                if retained is None:return self.reply(404,{'error':'RETAINED_AUTHORITY_REQUIRED'})
                return retained.do_POST(self)
            if self.path.startswith('/v1/terminal-evidence/'):
                if terminals is None:return self.reply(404,{'error':'TERMINAL_AUTHORITY_REQUIRED'})
                return terminals.do_POST(self)
            if self.path.startswith('/v1/document-drafts/'):
                if drafts is None:return self.reply(404,{'error':'DOCUMENT_PARENT_BRIDGE_REQUIRED'})
                return drafts.do_POST(self)
            if self.path.startswith('/v1/model-residency/'):
                if models is None:return self.reply(404,{'error':'MODEL_AUTHORITY_REQUIRED'})
                return models.do_POST(self)
            return requests.do_POST(self)
    return Combined
