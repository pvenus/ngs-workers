"""Optional Request-service residency routes and worker HTTP port; no startup.

Host the handler inside the Request service, not a new scheduler/worker DB.
Use a separate durable journal_path from the execution-claim journal.
"""
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler
import hmac
import json
import re
import urllib.error
import urllib.request
from ..contracts import PROFILES
from .model_residency import ModelLease
from .request_http import HttpRequests


def handler(authority, identities):
    identities = dict(identities)
    if any(not isinstance(k, str) or not k or role not in PROFILES or role not in authority.models
           for k, (_, role) in identities.items()):
        raise ValueError('MODEL_IDENTITIES_INVALID')

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, status, value):
            data = json.dumps(value, separators=(',', ':')).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            if (self.client_address[0] != '127.0.0.1' or self.headers.get('Origin') is not None
                    or self.headers.get('Host') != '127.0.0.1:' + str(self.server.server_port)):
                return self.reply(403, {'error': 'MODEL_LOOPBACK_ONLY'})
            token = self.headers.get('Authorization', '')
            identity = next((v for k, v in identities.items()
                             if hmac.compare_digest(token.encode(), ('Bearer ' + k).encode())), None)
            if identity is None:
                return self.reply(401, {'error': 'MODEL_AUTH_REQUIRED'})
            worker_id, role = identity
            length = self.headers.get('Content-Length', '')
            if (self.headers.get('Content-Type') != 'application/json' or not length.isdecimal()
                    or not 0 < int(length) <= 65536):
                return self.reply(400, {'error': 'MODEL_BODY_INVALID'})
            self.connection.settimeout(5)
            try:
                body = json.loads(self.rfile.read(int(length)))
                if not isinstance(body, dict):
                    raise ValueError('MODEL_BODY_INVALID')
                if self.path == '/v1/model-residency/acquire':
                    if (set(body) != {'workerId', 'role', 'key'} or body['workerId'] != worker_id
                            or body['role'] != role or not isinstance(body['key'], str)
                            or not re.fullmatch('[a-f0-9]{32}', body['key'])):
                        raise ValueError('MODEL_SCOPE_MISMATCH')
                    lease = authority.acquire(PROFILES[role], worker_id, body['key'])
                    return self.reply(200, {'lease': asdict(lease) if lease else None})
                if set(body) != {'lease'}:
                    raise ValueError('MODEL_BODY_INVALID')
                lease = ModelLease(**body['lease'])
                if lease.worker_id != worker_id or lease.role != role:
                    raise ValueError('MODEL_SCOPE_MISMATCH')
                # Full immutable grant validation prevents a caller changing
                # model size/host/epoch while retaining its grant id.
                with authority.transaction():
                    authority._row(lease, allow_released=self.path == '/v1/model-residency/release')
                if self.path == '/v1/model-residency/renew':
                    return self.reply(200, {'renewed': authority.renew(lease)})
                if self.path == '/v1/model-residency/release':
                    authority.release(lease)
                    return self.reply(200, {'ok': True})
                raise ValueError('MODEL_OPERATION_INVALID')
            except (ValueError, TypeError, KeyError) as exc:
                code = str(exc)
                if not re.fullmatch('[A-Z0-9_]{1,160}', code):
                    code = 'MODEL_CONTRACT_ERROR'
                return self.reply(409, {'error': code})
    return Handler


class HttpModelResidency(HttpRequests):
    """Shares durable-journal helpers, never execution claims or their journal."""
    def _call(self, operation, body):
        request = urllib.request.Request(self.base + '/v1/model-residency/' + operation,
            data=json.dumps(body, separators=(',', ':')).encode(),
            headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + self.token})
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read(65537)
                if len(raw) > 65536:
                    raise RuntimeError('MODEL_API_RESPONSE_TOO_LARGE')
                return json.loads(raw)
        except urllib.error.HTTPError as exc:
            raise RuntimeError('MODEL_API_HTTP_' + str(exc.code)) from None

    def acquire(self, profile, worker_id):
        if worker_id != self.worker_id or PROFILES.get(profile.role) != profile:
            raise ValueError('MODEL_SCOPE_MISMATCH')
        body = {'workerId': worker_id, 'role': profile.role, 'key': self._claim_key()}
        try:
            result = self._call('acquire', body)
        except OSError:
            result = self._call('acquire', body)
        if result['lease'] is None:
            self._save_key(None)
            return None
        lease = ModelLease(**result['lease'])
        if lease.worker_id != worker_id or lease.role != profile.role:
            raise ValueError('MODEL_SCOPE_MISMATCH')
        return lease

    def renew(self, lease):
        return self._call('renew', {'lease': asdict(lease)})['renewed'] is True

    def release(self, lease):
        self._call('release', {'lease': asdict(lease)})
        self._save_key(None)
