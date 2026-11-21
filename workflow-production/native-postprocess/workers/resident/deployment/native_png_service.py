"""Explicit trusted Request composition overlay; no default selection or new API.

All factories create DB-bound objects inside the current ThreadAuthority scope.
Existing native type proof/coverage must be supplied, never synthesized here.
"""
from contextlib import contextmanager
from threading import local
from ..adapters.native_png_workspace import RAW,ALPHA,PIXEL

SCHEMAS={RAW,ALPHA,PIXEL}
def bind_native_service(bridge,domain,settings,*,workflow_factory,verify_frozen,proof,recovery_reader,workspace,guard_resolver,parent_loop):
 enabled=settings.get('nativePNG',False)
 if type(enabled)is not bool:raise ValueError('NATIVE_PNG_OPT_IN_INVALID')
 if not enabled:return domain
 factories=(workflow_factory,verify_frozen,proof,recovery_reader,workspace,guard_resolver)
 if not all(callable(f)for f in factories)or (parent_loop is not None and not callable(getattr(parent_loop,'tick',None))):raise ValueError('NATIVE_SERVICE_BINDINGS_REQUIRED')
 if bridge.claim_preparation is not None:raise ValueError('NATIVE_SERVICE_ALREADY_BOUND')
 required=('selector','verify_publication','guard_resolver','inspect_publication','verify_recovery_publication','cleanup')
 if any(not callable(domain.get(k))for k in required):raise ValueError('REQUEST_SITE_DOMAIN_PORTS_REQUIRED')
 old=dict(domain);out=dict(domain);state=local();bridge.native_png=True
 def native(c):return c.parameters.get('schemaVersion')in SCHEMAS
 @contextmanager
 def prepare(profile):
  state.selection=None
  try:
   if profile.role=='animation':state.selection=workflow_factory().prepare_frozen_resident(profile.role,verify_frozen=verify_frozen)
   yield
  finally:state.selection=None
 bridge.claim_preparation=prepare
 def select(profile):
  w=workflow_factory()
  value=w.select_prepared_frozen_resident(profile.role,getattr(state,'selection',None))if profile.role=='animation'else w.select_for_resident(profile.role)
  return value if value is not None else old['selector'](profile)
 out['selector']=select
 out['verify_publication']=lambda c,r:proof(c,r)if native(c)else old['verify_publication'](c,r)
 out['guard_resolver']=lambda c:guard_resolver(c)if native(c)else old['guard_resolver'](c)
 out['inspect_publication']=lambda c:recovery_reader(c)(c)if native(c)else old['inspect_publication'](c)
 out['verify_recovery_publication']=lambda c,r:recovery_reader(c).verify(c,r)if native(c)else old['verify_recovery_publication'](c,r)
 out['cleanup']=lambda c:workspace(c).cleanup(c,workspace(c)._folder(c))if native(c)else old['cleanup'](c)
 if callable(old.get('resume_publication')):
  out['resume_publication']=lambda c,h:None if native(c)else old['resume_publication'](c,h)
 if parent_loop is not None:out['native_png_loop']=parent_loop
 return out
