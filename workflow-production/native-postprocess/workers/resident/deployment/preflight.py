"""Read-only cutover checks. No selector writes, migrations, process control or secrets."""
import hashlib,json,sqlite3
from pathlib import Path


def pinned_json(path,sha256):
 p=Path(path)
 if not p.is_absolute()or p.is_symlink()or p.resolve()!=p or not p.is_file():raise ValueError('PREFLIGHT_PATH_INVALID')
 raw=p.read_bytes()
 if hashlib.sha256(raw).hexdigest()!=sha256:raise ValueError('PREFLIGHT_PIN_CHANGED')
 value=json.loads(raw)
 if not isinstance(value,dict):raise ValueError('PREFLIGHT_JSON_INVALID')
 return value

class ActivationPreflight:
 def __init__(self,plan,*,consumer_process_proof):
  if not callable(consumer_process_proof):raise ValueError('PREFLIGHT_CONSUMER_PROOF_REQUIRED')
  self.plan,self.consumer_process_proof=plan,consumer_process_proof
 def __call__(self):
  p=self.plan
  selector=pinned_json(p['selectorPath'],p['selectorSha256'])
  if any(selector.get(k)!=p['selected'][k]for k in ('sourceRoot','runtimeRoot','operationalRuntimeRoot','requestDataBinding','displayDataBinding')):raise ValueError('PREFLIGHT_SELECTED_RELEASE_MISMATCH')
  binding=pinned_json(selector['requestDataBinding'],p['requestBindingSha256'])
  display=pinned_json(selector['displayDataBinding'],p['displayBindingSha256'])
  if any(binding.get(k)!=selector[k]or display.get(k)!=selector[k]for k in ('sourceRoot','runtimeRoot')):raise ValueError('PREFLIGHT_BINDING_RELEASE_MISMATCH')
  for key,expected in p['databaseIdentities'].items():
   path=Path(binding[key]);info=path.stat()
   if path.is_symlink()or path.resolve()!=path or (info.st_dev,info.st_ino)!=(expected['device'],expected['inode']):raise ValueError('PREFLIGHT_DATABASE_IDENTITY_CHANGED')
  cfg=pinned_json(p['consumerConfigPath'],p['consumerConfigSha256'])
  handoff=cfg.get('residentHandoff',{})
  if handoff.get('schemaVersion')!='resident-handoff.v1'or handoff.get('enabled')is not True or any(route not in handoff.get('routes',[]) for route in p['routes']):raise ValueError('PREFLIGHT_LEGACY_ROUTE_NOT_EXCLUDED')
  ops=pinned_json(p['opsConfigPath'],p['opsConfigSha256'])
  service=next((v for v in ops.get('services',[])if v.get('id')=='consumer'),None)
  if not service or service.get('command')!=p['consumerCommand']:raise ValueError('PREFLIGHT_CONSUMER_COMMAND_CHANGED')
  # Config files alone do not prove that an old process has reloaded them.
  if self.consumer_process_proof(p)is not True:raise ValueError('PREFLIGHT_OLD_CONSUMER_OWNERSHIP_UNKNOWN')
  db=Path(binding['requestDatabase'])
  with sqlite3.connect(db.as_uri()+'?mode=ro',uri=True)as conn:
   # Expiry is not process-death proof. Reject executable leases in the selected scope.
   scopes=p.get('activeScopeRoutes')
   if scopes is None:
    active=conn.execute("SELECT count(*) FROM common_requests WHERE worker IS NOT NULL OR state IN ('processing','synchronizing')").fetchone()[0]
   else:
    if not isinstance(scopes,list)or not scopes or any(not isinstance(v,list)or len(v)!=3 for v in scopes):raise ValueError('PREFLIGHT_ACTIVE_SCOPE_INVALID')
    condition=' OR '.join('(r.request_type=? AND f.schema_version=? AND f.domain=?)' for _ in scopes)
    active=conn.execute("SELECT count(DISTINCT r.request_id) FROM common_requests r JOIN common_payload_refs f ON f.request_id=r.request_id WHERE (r.worker IS NOT NULL OR r.state IN ('processing','synchronizing')) AND ("+condition+")",tuple(x for route in scopes for x in route)).fetchone()[0]
   if active:raise ValueError('PREFLIGHT_INFLIGHT_REQUESTS')
  return True
