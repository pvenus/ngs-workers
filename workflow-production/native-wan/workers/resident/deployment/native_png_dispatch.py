"""Opt-in mixed dispatch on existing roles; no duplicated worker or model lease.

The native Ports must use the SAME existing request, model and evidence clients.
Payload chooses only an exact reviewed schema, never a class, path or callback.
"""
from .composition import Ports
from ..adapters.native_png_workspace import RAW,ALPHA,PIXEL
class Backend:
 def __init__(self,legacy,native):self.backends=(legacy,native);self.index=0;self.claim=None
 def target(self,c):return int(c.parameters.get('schemaVersion')in (RAW,ALPHA,PIXEL))
 def needs_switch(self,c):return self.target(c)!=self.index
 def select_claim(self,c):self.index=self.target(c);self.claim=c
 def __getattr__(self,name):return getattr(self.backends[self.index],name)
 def load(self):return self.backends[self.index].load()
 def unload(self):return self.backends[self.index].unload()
 def execute(self,c,w,cancel):
  if c!=self.claim or self.target(c)!=self.index:raise ValueError('NATIVE_BACKEND_CLAIM_CHANGED')
  return self.backends[self.index].execute(c,w,cancel)
 def requires_model_for(self,c):
  b=self.backends[self.target(c)];f=getattr(b,'requires_model_for',None)
  return bool(f(c))if callable(f)else bool(getattr(b,'requires_model',False))
class Workspace:
 def __init__(self,backend,legacy,native):self.backend=backend;self.workspaces=(legacy,native)
 def target(self,c):
  if self.backend.claim!=c or self.backend.target(c)!=self.backend.index:raise ValueError('NATIVE_WORKSPACE_CLAIM_CHANGED')
  return self.workspaces[self.backend.index]
 def stage(self,c):return self.target(c).stage(c)
 def publish(self,c,w,a):return self.target(c).publish(c,w,a)
 def cleanup(self,c,w):return self.target(c).cleanup(c,w)
def mixed_native_ports(legacy,native,*,enabled=False):
 if type(enabled)is not bool:raise ValueError('NATIVE_PNG_OPT_IN_INVALID')
 if not enabled:return legacy
 if not isinstance(legacy,Ports)or not isinstance(native,Ports)or any(getattr(legacy,k)is not getattr(native,k)for k in ('requests','residency','evidence')):raise ValueError('NATIVE_SHARED_PORTS_REQUIRED')
 b=Backend(legacy.backend,native.backend)
 return Ports(legacy.requests,Workspace(b,legacy.pds,native.pds),b,legacy.residency,legacy.evidence)
