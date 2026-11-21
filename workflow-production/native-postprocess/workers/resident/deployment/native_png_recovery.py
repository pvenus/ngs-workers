"""Default-off selection for the existing service reconciliation callbacks.

No worker HTTP method and no current-claim storage caller are reused here.
The service must route only the original raw/alpha claim to this reader; retain
its existing readers for other claims. Supervisor remains the sole state writer.
"""
from ..adapters.native_png_recovery import NativePNGRecoveryReader

def select_native_recovery(settings,bindings):
 enabled=settings.get('nativePNG',False)
 if type(enabled)is not bool:raise ValueError('NATIVE_PNG_OPT_IN_INVALID')
 if not enabled:return None
 required=('authority','workflow','workspace','readback','read_asset')
 if any(k not in bindings for k in required):raise ValueError('WORKER_TRUSTED_BINDING_REQUIRED')
 return NativePNGRecoveryReader(bindings['authority'],bindings['workflow'],bindings['workspace'],
        readback=bindings['readback'],read_asset=bindings['read_asset'],enabled=True)
