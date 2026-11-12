"""API and authenticated loopback bridge for explicit local browser input.

This authenticates the local browser channel, not a person's real-world identity.
An untrusted process with the same OS account is outside this security boundary.
"""
import hashlib
import hmac
import json
import os
import secrets
import time
import uuid
from pathlib import Path
import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from .config import DATA_DIR
from .listening_store import GroupRequest, Mutation, ListeningError, ListeningStore

KEY_PATH = DATA_DIR / '.review-bridge-key'

def bridge_key(path=KEY_PATH):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(secrets.token_bytes(32))
    if path.is_symlink() or path.stat().st_mode & 0o077:
        raise RuntimeError('review bridge key must be a private regular file')
    key = path.read_bytes()
    if len(key) != 32:
        raise RuntimeError('invalid review bridge key')
    return key

def signed_headers(key, path, body):
    stamp, request_id = str(int(time.time())), uuid.uuid4().hex
    content = '\n'.join((stamp, request_id, path, hashlib.sha256(body).hexdigest()))
    return {'X-Review-Time': stamp, 'X-Review-Request': request_id,
            'X-Review-Signature': hmac.new(key, content.encode(), hashlib.sha256).hexdigest()}

def verify_signature(request, body, key):
    stamp = request.headers.get('x-review-time', '')
    request_id = request.headers.get('x-review-request', '')
    try:
        if abs(time.time() - int(stamp)) > 60 or len(request_id) != 32:
            return None
    except ValueError:
        return None
    content = '\n'.join((stamp, request_id, request.url.path, hashlib.sha256(body).hexdigest()))
    expected = hmac.new(key, content.encode(), hashlib.sha256).hexdigest()
    return request_id if hmac.compare_digest(expected, request.headers.get('x-review-signature', '')) else None

def validate_tasks(request: GroupRequest):
    """Verify linked immutable task IDs exist in the same project, without modifying tasks."""
    task_ids = [str(request.comparison_task_id), str(request.human_listening_task_id)]
    task_ids.extend(str(c.generation_task_id) for c in request.candidates)
    if len(set(task_ids)) != len(task_ids):
        raise ListeningError(422, 'comparison, listening and generation tasks must be distinct')
    projects = set()
    try:
        with httpx.Client(base_url=os.getenv('TASK_SERVICE_API_URL', 'http://127.0.0.1:8768'), timeout=10, trust_env=False) as client:
            for task_id in task_ids:
                response = client.get('/v1/tasks/' + task_id)
                if response.status_code == 404:
                    raise ListeningError(422, 'linked task does not exist: ' + task_id)
                response.raise_for_status()
                projects.add(response.json()['project_key'])
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        raise ListeningError(503, 'cannot verify linked tasks') from exc
    if len(projects) != 1:
        raise ListeningError(422, 'linked tasks must belong to one project')

def install_listening(app, catalog, *, key=None, environment='production', task_validator=validate_tasks):
    store = ListeningStore(catalog, environment)
    app.state.listening = store
    key = key if key is not None else bridge_key()
    router = APIRouter(prefix='/v1/listening')

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Non-finite input cannot itself be emitted as valid JSON in an error response.
        return JSONResponse(status_code=422, content={'detail': [
            {k: e[k] for k in ('loc', 'msg', 'type')} for e in exc.errors()]})

    @app.exception_handler(ListeningError)
    async def listening_error(request, exc):
        return JSONResponse(status_code=exc.status, content={'detail': exc.detail, 'current': exc.current})

    @router.get('/groups')
    def groups():
        return {'groups': store.groups()}

    @router.post('/groups')
    def create_group(payload: GroupRequest):
        task_validator(payload)
        return store.save_group(payload)

    @router.put('/groups/{gid}')
    def update_group(gid: str, payload: GroupRequest):
        task_validator(payload)
        return store.save_group(payload, gid)

    @router.get('/groups/{gid}')
    def group(gid: str):
        return store.group(gid)

    @router.get('/groups/{gid}/tracks')
    def tracks(gid: str, decision_filter: str = 'visible', include_archived: bool = False):
        result = store.tracks(gid, decision_filter, include_archived)
        # Original technical review and human evaluation remain separately addressable.
        result['tracks'] = [dict(catalog.get(s['song_id']), listening=s) for s in result['tracks']]
        return result

    @router.get('/groups/{gid}/tracks/{sid}')
    def state(gid: str, sid: str, history: bool = False):
        return store.get(gid, sid, history)

    @router.post('/groups/{gid}/tracks/{sid}')
    async def mutate(gid: str, sid: str, request: Request, payload: Mutation):
        source_request_id = verify_signature(request, await request.body(), key)
        return store.mutate(gid, sid, payload, trusted_ui=source_request_id is not None,
                            source_request_id=source_request_id)

    app.include_router(router)
