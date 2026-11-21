"""Service-side scheduler for the existing formal submit/observe coordinator.

The supplied pinned source OneStop owns its durable plan and attempts. This loop
never builds commands, accepts requests, applies results or invokes workers.
The deployment must use the same owns predicate to exclude legacy consumers.
"""
import json
from threading import Event
from .request_selector import digest


class OneStopLoop:
    def __init__(self, common, service_for_parent, *, owns, preflight, poll_seconds=1):
        if not all(callable(p) for p in (service_for_parent, owns, preflight)):
            raise ValueError('ONE_STOP_SERVICE_PORT_REQUIRED')
        self.common, self.service_for_parent = common, service_for_parent
        self.owns, self.preflight = owns, preflight
        self.poll_seconds = poll_seconds
        self.stop = Event()
        self.last_errors = {}

    def tick(self):
        if self.preflight() is not True:
            raise ValueError('ONE_STOP_EXCLUSIVE_OWNER_REQUIRED')
        rows = self.common.db.execute("""SELECT c.* FROM common_requests c
            WHERE c.request_type='npc.workflow' AND c.source='npc-one-stop'
            AND c.state IN ('queued','retry_wait','processing') AND c.retry_at<=?
            ORDER BY c.created_at,c.request_id""", (self.common.clock(),)).fetchall()
        results = []
        for raw in rows:
            row = dict(raw)
            if self.owns(row) is not True:
                continue
            rid = row['request_id']
            try:
                refs = self.common.db.execute('SELECT * FROM common_payload_refs WHERE request_id=?', (rid,)).fetchall()
                exact = [dict(r) for r in refs if r['domain']=='npc-one-stop' and r['schema_version']=='ngs.npc-one-stop.v1']
                stored = self.common.db.execute('SELECT * FROM npc_one_stop_requests WHERE request_id=?', (rid,)).fetchone()
                if len(exact)!=1 or stored is None:
                    raise ValueError('ONE_STOP_ORIGINAL_REQUEST_REQUIRED')
                ref = exact[0]
                if (ref['reference_id']!=rid or stored['actor']!=row['requester']
                    or ref['input_hash']!=stored['input_hash']
                    or digest(json.loads(stored['body']))!=stored['input_hash']):
                    raise ValueError('ONE_STOP_ORIGINAL_BINDING_CHANGED')
                service = self.service_for_parent(rid)
                if service.common is not self.common:
                    raise ValueError('ONE_STOP_AUTHORITY_CHANGED')
                result = service.tick(rid, row['requester'])
                results.append((rid, result['state']))
                self.last_errors.pop(rid, None)
            except BlockingIOError:
                continue
            except Exception as exc:
                self.last_errors[rid] = type(exc).__name__
        return results

    def serve(self):
        while not self.stop.is_set():
            try:
                self.tick()
            except Exception as exc:
                self.last_errors['loop'] = type(exc).__name__
            self.stop.wait(self.poll_seconds)
