"""Local HTTP bridge to the gateway; no local catalog or database access."""
from urllib.parse import urlsplit
import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response


class ReviewGatewayClient:
    """Fetch review projections and audio from one local gateway authority."""
    def __init__(self, base_url, transport=None):
        parsed = urlsplit(base_url)
        if (parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost')
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in ('', '/')):
            raise ValueError('review gateway must be a local HTTP origin')
        self.base_url = base_url.rstrip('/')
        self.transport = transport

    def fetch(self, path, range_header=None, *, method='GET', payload=None):
        headers = {'Range': range_header} if range_header else {}
        try:
            with httpx.Client(base_url=self.base_url, timeout=30, trust_env=False,
                              follow_redirects=False, transport=self.transport) as client:
                response = client.request(method,path,headers=headers,json=payload)
        except httpx.RequestError as exc:
            raise HTTPException(503, '게이트웨이에 연결할 수 없습니다.') from exc
        if response.status_code not in (200, 206, 404, 416, 422):
            raise HTTPException(502, '게이트웨이 응답을 확인할 수 없습니다.')
        allowed = ('content-type', 'content-range', 'accept-ranges', 'content-disposition')
        return Response(content=response.content, status_code=response.status_code,
                        headers={k: v for k, v in response.headers.items() if k in allowed})


def create_gateway_router(client):
    """Expose existing player paths while obtaining all data through the gateway."""
    router = APIRouter()

    @router.get('/v1/reviews')
    def reviews(include_archived: bool = False):
        return client.fetch('/v1/reviews?include_archived=' + str(include_archived).lower())

    def mutation_allowed(request: Request):
        origin = request.headers.get('origin')
        expected = str(request.base_url).rstrip('/')
        # The gateway sets this header after authentication. The review
        # process itself only listens on loopback; browsers cannot add it in a
        # cross-origin form request.
        proxy_origin = request.headers.get('x-forge-public-origin', '')
        if proxy_origin.startswith(('http://', 'https://')):
            expected = proxy_origin.rstrip('/')
        if origin != expected or request.headers.get('x-library-action') != '1':
            raise HTTPException(403, '청취실에서만 변경할 수 있습니다.')

    @router.post('/v1/library/{song_id}/archive')
    def archive(song_id: str, request: Request):
        mutation_allowed(request)
        return client.fetch('/v1/gateway/tracks/' + checked_id(song_id) + '/archive',method='POST')

    @router.post('/v1/library/{song_id}/restore')
    def restore(song_id: str, request: Request):
        mutation_allowed(request)
        return client.fetch('/v1/gateway/tracks/' + checked_id(song_id) + '/restore',method='POST')

    @router.put('/v1/library/{song_id}/tags')
    async def tags(song_id: str, request: Request):
        mutation_allowed(request)
        payload = await request.json()
        return client.fetch('/v1/gateway/tracks/' + checked_id(song_id) + '/tags',method='PUT',payload=payload)

    @router.get('/v1/reviews/{artifact_id}')
    def review(artifact_id: str):
        return client.fetch('/v1/reviews/' + checked_id(artifact_id))

    @router.get('/v1/reviews/{artifact_id}/audio')
    def audio(artifact_id: str, request: Request):
        return client.fetch('/v1/reviews/' + checked_id(artifact_id) + '/audio',
                            request.headers.get('range'))

    @router.get('/v1/reviews/{artifact_id}/download')
    def download(artifact_id: str):
        return client.fetch('/v1/reviews/' + checked_id(artifact_id) + '/download')

    return router


def checked_id(value):
    """Only artifact IDs are accepted, never upstream paths or URLs."""
    import re
    if not re.fullmatch(r'[0-9a-f]{32}', value):
        raise HTTPException(404, '잘못된 음원 ID입니다.')
    return value
