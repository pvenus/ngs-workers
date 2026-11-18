"""Read only an existing intake-pinned ownership session on the Request service.

The trusted domain resolver validates parent/child membership and returns the
original Session object. This adapter never pins, admits, renews, or creates a
new ownership scope. Workers receive the guarded API response, not local paths.
"""
import json
class PinnedOwnershipGuardResolver:
    def __init__(self,session_for_claim):
        if not callable(session_for_claim):raise ValueError('OWNERSHIP_SESSION_RESOLVER_REQUIRED')
        self.session_for_claim=session_for_claim
    def __call__(self,claim):
        session=self.session_for_claim(claim)
        path=session.path
        if path.is_symlink() or not path.is_file():raise ValueError('NPC_OWNERSHIP_INTAKE_PIN_REQUIRED')
        with session.locked():
            if path.is_symlink():raise ValueError('NPC_OWNERSHIP_JOURNAL_PATH')
            state=json.loads(path.read_text())
        guard=state.get('guard')
        if (state.get('released') or state.get('npcId')!=session.npc or state.get('requestId')!=session.rid
                or not isinstance(guard,dict) or any(guard.get(k)!=state.get(k) for k in ('npcId','requestId','generationId'))
                or not guard.get('leaseToken')):raise ValueError('NPC_OWNERSHIP_ORIGINAL_PIN_REQUIRED')
        return {'guard':guard,'stageRequestId':claim.request_id,'scopeRequestId':session.rid}
