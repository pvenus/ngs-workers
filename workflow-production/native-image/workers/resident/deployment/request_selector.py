"""Request-service-only deterministic payload selection across ready requests.

Resolvers/validators are trusted per exact route. Hashes bind the entire original
payload envelope. This module never unwraps legacy documents or claims a worker.
"""
import hashlib,json


def digest(v):return hashlib.sha256(json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

class FormalPayloadSelector:
    def __init__(self,common,routes,*,ready):
        # route tuple -> (capability, resolver(row,ref), validator(original_payload))
        self.common,self.routes,self.ready=common,dict(routes),ready
    def __call__(self,profile):
        rows=self.common.db.execute("SELECT * FROM common_requests WHERE state IN ('queued','retry_wait') AND retry_at<=? ORDER BY created_at,request_id",(self.common.clock(),)).fetchall()
        fenced=self.common.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='resident_failure_fences'").fetchone()is not None
        for raw in rows:
            row=dict(raw)
            if fenced and self.common.db.execute('SELECT 1 FROM resident_failure_fences WHERE request_id=?',(row['request_id'],)).fetchone():continue
            if not self.ready(row):continue
            refs=[dict(v)for v in self.common.db.execute('SELECT * FROM common_payload_refs WHERE request_id=?',(row['request_id'],))]
            matches=[(ref,self.routes[(row['request_type'],ref['schema_version'],ref['domain'])]) for ref in refs if (row['request_type'],ref['schema_version'],ref['domain']) in self.routes]
            if len(matches)>1:raise ValueError('FORMAL_ROUTE_AMBIGUOUS')
            if not matches:continue
            ref,(capability,resolver,validator)=matches[0]
            if capability not in profile.capabilities:continue
            selected=resolver(row,ref)
            payload=selected['parameters']
            if selected.get('requestId')!=row['request_id'] or digest(payload)!=ref['input_hash']:raise ValueError('FORMAL_PAYLOAD_BINDING_INVALID')
            if validator(payload) is False:raise ValueError('FORMAL_PAYLOAD_VALIDATION_FAILED')
            if not isinstance(selected.get('inputRefs'),list) or any(not isinstance(v,str)or not v for v in selected['inputRefs']):raise ValueError('FORMAL_INPUT_REFS_INVALID')
            revision=selected.get('expectedRevision')
            if not isinstance(revision,str)or not revision:raise ValueError('FORMAL_REVISION_REQUIRED')
            return {**selected,'requestId':row['request_id'],'requestType':row['request_type'],
                    'schemaVersion':ref['schema_version'],'domain':ref['domain'],
                    'capability':capability,'inputHash':ref['input_hash']}
        return None
