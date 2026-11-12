"""Same-origin browser session boundary; all records are stored by the gateway."""
import json
import secrets
import time
from urllib.parse import quote
import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from .listening_api import bridge_key, signed_headers

ALLOWED_ORIGINS = {'http://127.0.0.1:8766', 'http://localhost:8766'}

def create_listening_bridge(client, *, key=None, origins=None):
    key = key if key is not None else bridge_key()
    origins = origins if origins is not None else ALLOWED_ORIGINS
    sessions = {}
    router = APIRouter(prefix='/v1/listening')

    def same_origin(request):
        origin = request.headers.get('origin')
        if (origin not in origins or origin != str(request.base_url).rstrip('/') or
            request.headers.get('sec-fetch-site') != 'same-origin' or
            not request.headers.get('content-type', '').startswith('application/json')):
            raise HTTPException(403, 'same-origin JSON browser request required')

    @router.post('/session')
    def session(request: Request):
        same_origin(request)
        stamp = time.time()
        for token in list(sessions):
            if sessions[token]['expires'] < stamp:
                del sessions[token]
        if len(sessions) >= 1000:
            raise HTTPException(429, 'too many review sessions')
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        sessions[token] = {'csrf': csrf, 'expires': stamp + 3600, 'origin': request.headers['origin']}
        response = JSONResponse({'csrf_token': csrf, 'expires_in': 3600, 'actor_id': 'local-review-user',
                                 'identity_assurance': 'local-browser-session'})
        response.set_cookie('review_session', token, max_age=3600, httponly=True, samesite='strict', path='/v1/listening')
        return response

    def proxy(method, path, *, body=None, headers=None):
        try:
            with httpx.Client(base_url=client.base_url, transport=client.transport, timeout=30,
                              trust_env=False, follow_redirects=False) as upstream:
                response = upstream.request(method, path, content=body, headers=headers)
        except httpx.RequestError as exc:
            raise HTTPException(503, '게이트웨이에 연결할 수 없습니다. 같은 키로 다시 시도하세요.') from exc
        return Response(response.content, status_code=response.status_code, media_type='application/json',
                        headers={'Cache-Control': 'no-store'})

    def checked(value):
        import re
        if not re.fullmatch('[0-9a-f]{32}', value):
            raise HTTPException(404, 'invalid ID')
        return value

    @router.get('/groups')
    def groups():
        return proxy('GET', '/v1/listening/groups')

    @router.get('/groups/{gid}')
    def group(gid: str):
        return proxy('GET', '/v1/listening/groups/' + checked(gid))

    @router.get('/groups/{gid}/tracks')
    def tracks(gid: str, decision_filter: str = 'visible', include_archived: bool = False):
        return proxy('GET', '/v1/listening/groups/' + checked(gid) + '/tracks?decision_filter=' + quote(decision_filter, safe='') + '&include_archived=' + str(include_archived).lower())

    @router.get('/groups/{gid}/tracks/{sid}')
    def track(gid: str, sid: str, history: bool = False):
        return proxy('GET', '/v1/listening/groups/' + checked(gid) + '/tracks/' + checked(sid) + '?history=' + str(history).lower())

    @router.post('/groups/{gid}/tracks/{sid}')
    async def mutate(gid: str, sid: str, request: Request):
        same_origin(request)
        session = sessions.get(request.cookies.get('review_session', ''))
        if (not session or session['expires'] < time.time() or session['origin'] != request.headers['origin'] or
            not secrets.compare_digest(session['csrf'], request.headers.get('x-review-csrf', ''))):
            raise HTTPException(403, 'review session or CSRF token expired')
        body = await request.body()
        if len(body) > 200000:
            raise HTTPException(413, 'review input too large')
        path = '/v1/listening/groups/' + checked(gid) + '/tracks/' + checked(sid)
        headers = signed_headers(key, path, body)
        headers['Content-Type'] = 'application/json'
        return proxy('POST', path, body=body, headers=headers)

    return router
