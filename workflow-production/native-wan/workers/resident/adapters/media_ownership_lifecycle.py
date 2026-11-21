"""Request-service ownership lifecycle for an existing formal intake pin.

No pin creation, environment bypass or worker-granted authority. Native producer
coverage and terminal receipts remain mandatory; unknowns keep their ownership.
"""
import json
from threading import RLock

class MediaOwnershipLifecycle:
    def __init__(self,*,configure,producer,verified,failure_reader=None):
        if not all(callable(v) for v in (configure,producer,verified)):
            raise ValueError('MEDIA_NATIVE_PRODUCER_REQUIRED')
        self.configure,self.producer,self.verified=configure,producer,verified
        self.failure_reader=failure_reader;self.lock=RLock()
    def _state(self,session):
        if session.path.is_symlink() or not session.path.is_file():
            raise ValueError('MEDIA_ORIGINAL_OWNERSHIP_PIN_REQUIRED')
        with session.locked():state=json.loads(session.path.read_text())
        if (state.get('npcId')!=session.npc or state.get('requestId')!=session.rid
                or not state.get('generationId')):raise ValueError('MEDIA_ORIGINAL_PIN_CHANGED')
        guard=state.get('guard')
        if guard is not None and (not isinstance(guard,dict) or not guard.get('leaseToken')
                or any(guard.get(k)!=state[k] for k in ('npcId','requestId','generationId'))):
            raise ValueError('MEDIA_ORIGINAL_GUARD_CHANGED')
        return state
    def _configure(self,service,rid):
        if self.configure(service,rid) is not True:raise ValueError('MEDIA_NATIVE_PRODUCER_NOT_CONFIGURED')
    def _output(self,service,session,state):
        self._configure(service,session.rid)
        output=self.producer(state['guard']);output.start();self.verified(output,state['guard'])
        return output
    def ensure(self,service,session,*,kind=None,path=None):
        with self.lock:
            state=self._state(session)
            if state.get('released'):raise ValueError('MEDIA_OWNERSHIP_ALREADY_RELEASED')
            # Admission recovers ONLY the generation pinned by original intake.
            # The native Session persists/compares the returned exact guard.
            if not state.get('guard'):
                self._configure(service,session.rid)
                session.enter();state=self._state(session)
            output=self._output(service,session,state)
            if path is not None:
                if kind not in ('frame-job','animation-job'):raise ValueError('MEDIA_WORKSPACE_KIND_INVALID')
                output.register_workspace(kind,path);self.verified(output,state['guard'])
            return state['guard']
    def finalize(self,service,session):
        with self.lock:
            state=self._state(session);row=service.common.row(session.rid)
            if row['worker'] is not None or row['state'] not in ('completed','blocked','failed','conflict'):
                return False
            if state.get('released'):return True
            if not state.get('guard'):return False
            output=self._output(service,session,state)
            if row['state']=='completed':
                view=service.view(session.rid,row['requester'])
                if view.get('publicationState')!='published' or view.get('publication',{}).get('verified') is not True:
                    raise ValueError('MEDIA_PUBLISHED_MIRROR_REQUIRED')
                result=output.finish_published(view) # Native authoritative receipt reader.
            else:
                if not callable(self.failure_reader):return False
                result=output.finish_failed({'revision':row['revision']},proof_reader=self.failure_reader)
            if not isinstance(result,dict) or result.get('localLeaseReleased') is not True or result.get('globalLeaseReleased') is not False:
                raise ValueError('MEDIA_LOCAL_COMPLETION_UNVERIFIED')
            session.state=state;session.finish()
            return self._state(session).get('released') is True
