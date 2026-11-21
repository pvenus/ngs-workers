"""Explicit local factory; never consulted by the default MP4 role factory."""
def select_native_alpha(settings,bindings):
 enabled=settings.get('nativePNG',False)
 if type(enabled)is not bool:raise ValueError('NATIVE_PNG_OPT_IN_INVALID')
 if not enabled:return None
 config=bindings.get('full_frame_config')
 if type(config)is not dict:raise ValueError('WORKER_TRUSTED_BINDING_REQUIRED')
 from ..backends.frame_native_alpha_model import NativeAlphaModel
 return NativeAlphaModel(**config)

def select_native_animation(settings,bindings):
 enabled=settings.get('nativePNG',False)
 if type(enabled)is not bool:raise ValueError('NATIVE_PNG_OPT_IN_INVALID')
 if not enabled:return None
 config=bindings.get('animation_config');reader=bindings.get('native_source_reader')
 if config is None or not callable(reader):raise ValueError('WORKER_TRUSTED_BINDING_REQUIRED')
 from ..backends.animation_native_backend import NativeAnimationBackend
 return NativeAnimationBackend(config,read_source=reader,residency_root=settings.get('modelResidencyRoot'))
