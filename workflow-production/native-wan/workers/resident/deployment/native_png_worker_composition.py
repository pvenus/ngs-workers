"""Additive trusted local role composition; credentials remain in original factory.

Call with the original reviewed role bindings. No configuration or file is
changed here. Returned factories construct ordinary clients, never load models.
"""
from .native_png_readback import NativePNGReadback
from .native_png_http_storage import NativePNGHTTPStorage
from ..adapters.native_png_inputs import stage_raw_inputs,read_claim_inputs
from ..adapters.native_png_workspace import NativePNGWorkspace

def with_native_worker(role,settings,legacy_bindings,*,alpha_inference=None):
 enabled=settings.get('nativePNG',False)
 if type(enabled)is not bool:raise ValueError('NATIVE_PNG_OPT_IN_INVALID')
 if not enabled:return legacy_bindings
 if role not in ('animation','postprocess'):raise ValueError('NATIVE_ROLE_UNSUPPORTED')
 if role=='postprocess' and (type(alpha_inference)is not dict or alpha_inference.get('fixture')is not False):
  raise ValueError('NATIVE_ALPHA_PROVENANCE_REQUIRED')
 def factory(requests):
  def current(c):
   g=requests.ownership_guard(c)
   if type(g)is not dict or set(g)!={'npcId','generationId','requestId','leaseToken'} or any(type(v)is not str or not v for v in g.values()):raise ValueError('NATIVE_WORKER_GUARD_REQUIRED')
   if g['npcId']!=c.parameters['npcId'] or g['generationId']!=c.parameters['generationId'] or g['requestId']!=c.parameters['parentRequestId']:raise ValueError('NATIVE_WORKER_GUARD_SCOPE')
   return True
  class PublicationCheck:
   authorize_publication=staticmethod(current)
  def client(c):return NativePNGReadback(settings['pdsUrl'],lambda:requests.ownership_guard(c),c.request_id)
  def source(c):
   reader=client(c)
   read_claim_inputs(reader,c,read_generation=reader.generation,assert_current=current)
   return c.parameters['frozenInputs']
  def workspace(_):
   from .role_factory import PerClaimWorkspace
   def build(c):
    reader=client(c)
    return NativePNGWorkspace(settings['scratchRoot'],requests=PublicationCheck(),
     storage=NativePNGHTTPStorage(settings['pdsUrl'],requests.ownership_guard),
     read_document=reader.document,read_asset=reader.asset,
     raw_source=lambda claim:stage_raw_inputs(reader,claim,read_generation=reader.generation,assert_current=current),
     refresh_host=lambda:requests.refresh_native_workspace(c))
   return PerClaimWorkspace(build)
  return {**legacy_bindings,'workspace_factory':workspace,'native_source_reader':source,'alpha_inference':alpha_inference}
 return {**legacy_bindings,'native_bindings':factory}
