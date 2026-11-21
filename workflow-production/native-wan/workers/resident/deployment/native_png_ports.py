"""Local explicit native composition; default role factories remain unchanged."""
from .composition import Ports
from ..adapters.native_png_workspace import NativePNGWorkspace
from ..backends.frame_native_backend import NativeFrameBackend
from .native_png_models import select_native_animation,select_native_alpha

def build_native_ports(role,settings,bindings):
 enabled=settings.get('nativePNG',False)
 if type(enabled)is not bool:raise ValueError('NATIVE_PNG_OPT_IN_INVALID')
 if not enabled:return None
 if role not in ('animation','postprocess'):raise ValueError('NATIVE_ROLE_UNSUPPORTED')
 def require(name):
  if name not in bindings:raise ValueError('WORKER_TRUSTED_BINDING_REQUIRED')
  return bindings[name]
 workspace_factory=bindings.get('workspace_factory')
 if workspace_factory is not None:
  if not callable(workspace_factory):raise ValueError('WORKER_TRUSTED_BINDING_REQUIRED')
  workspace=workspace_factory(require('requests'))
 else:workspace=NativePNGWorkspace(settings['scratchRoot'],requests=require('requests'),storage=require('storage'),read_document=require('read_document'),read_asset=require('read_asset'),raw_source=require('raw_source'),refresh_host=require('refresh_host'))
 backend=(select_native_animation(settings,bindings)if role=='animation'else NativeFrameBackend(select_native_alpha(settings,bindings),inference=require('alpha_inference')))
 return Ports(require('requests'),workspace,backend,require('residency'),require('evidence'))
