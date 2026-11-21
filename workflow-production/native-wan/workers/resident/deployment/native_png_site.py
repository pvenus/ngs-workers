"""Shared private-site template for Request and the existing owner close RPC.

Use the same exact selected site spec for both. This module reads no credential
files, opens no new service endpoint, and does not initialize on owner close.
"""
from pathlib import Path
from threading import RLock

from ..adapters.common_authority import CommonAuthorityBridge
from ..adapters.failure_fence import FailureFence
from ..adapters.trusted_process_evidence import TrustedProcessEvidence
from ..terminal_evidence import SourcePin
from .native_png_failure import bind_native_failure
from .native_png_request_composition import NativePNGRequestComposition

ROLES=('animation','postprocess')

def require(ok,code):
 if not ok:raise ValueError('NATIVE_SITE_'+code)

class NativePNGSite:
 def __init__(self,sources,site_spec,configuration,*,call=None):
  """The owning private factory supplies already pinned spec/config objects."""
  require(type(configuration)is dict and set(configuration)=={
      'ownershipRoot','ledgerIdentity','routes','sourcePins','scratchHostConfig'},'CONFIG')
  self.sources,self.spec,self.configuration=sources,site_spec,configuration
  from local.services.npc_scratch_bootstrap import private_json
  from local.services.npc_scratch_registry import checked
  c=private_json(configuration['scratchHostConfig'])
  require(c.get('enabled')is True and c.get('schemaVersion')=='npc-scratch-host.v1','SCRATCH_CONFIG')
  self.scratch=c
  r=configuration['routes']
  require(type(r)is dict and set(r)==set(ROLES),'ROUTES')
  for role,route in r.items():
   require(route.get('hostId')==c['hostId'] and route.get('rootKey')in c['roots']
           and checked(route['scratchRoot'])==checked(site_spec['roles'][role]['scratchRoot']),'ROUTE_SCOPE')
  registry=c['registry'];host=c['hostId']
  def selected(key,names):
   entries=registry.get(key)
   require(type(entries)in (list,dict) and set(names)<=set(entries),'REGISTRY')
   return {n:entries[n]for n in names} if type(entries)is dict else list(names)
  self.registry={**registry,'hosts':selected('hosts',(host,)),
                 'producers':selected('producers',('animation','frames'))}
  self.call=call or sources.modules['ownership'].api
  require(call is not None or site_spec['pdsUrl']=='http://127.0.0.1:8030','PDS')
  self.pins=tuple(SourcePin(**p)for p in configuration['sourcePins'])
  self.profiles={}
  for role in ROLES:
   key=site_spec.get('terminalBackendIds',{}).get(role)
   require(type(key)is str and bool(key) and key not in self.profiles,'TERMINAL_PROFILE')
   self.profiles[key]=(role,tuple(SourcePin(**p)for p in site_spec['roleTerminalPins'][role]))
  for key,entry in site_spec.get('historicalTerminalProfiles',{}).items():
   if entry['role']in ROLES:
    require(key not in self.profiles,'TERMINAL_COLLISION')
    self.profiles[key]=(entry['role'],tuple(SourcePin(**p)for p in entry['pins']))

 def _compose(self,bridge):
  from local.services.npc_scratch_runtime.coordinator import Coordinator
  from local.services.npc_scratch_runtime.pds import PDS
  c=self.scratch;cfg=self.configuration
  coord=Coordinator.open_existing(c['ledger'],c['roots'],c['quarantine'],PDS(self.call),identity=cfg['ledgerIdentity'])
  return NativePNGRequestComposition(self.sources,bridge,{'nativePNG':True},pds_url=self.spec['pdsUrl'],
    coordinator=coord,ownership_root=cfg['ownershipRoot'],roots=c['roots'],registry=self.registry,
    routes=cfg['routes'],source_pins=self.pins,call=self.call)

 def _same_common(self,common):
  path=Path(common.bound_database_path())
  require(path==Path(self.spec['requestDatabase']) and path.resolve()==path,'COMMON_BINDING')
  st=path.stat()
  require([st.st_dev,st.st_ino]==self.spec['requestDatabaseIdentity'],'COMMON_IDENTITY')

 def bind_request(self,bridge,domain,*,owns_parent):
  # The existing host verifies the selected DB on each thread's authorized open.
  # This composition itself does not force a connection outside that scope.
  composed=self._compose(bridge)
  return composed.bind(domain,one_stop_root=self.spec['oneStopRoot'],
      executor_id=self.spec['workflowExecutor'],owns_parent=owns_parent)

 def build_native_owner_adapter(self,common,children_root):
  self._same_common(common)
  require(Path(children_root)==Path(self.spec['oneStopRoot'])/'children','ONE_STOP_ROOT')
  def no_work(*_):raise ValueError('NATIVE_SITE_OWNER_NO_WORK')
  terminal=None
  bridge=CommonAuthorityBridge(common,RLock(),no_work,no_work,{'video-global':1,'cpu-image':1},
    verify_stopped=lambda claim:terminal.verify_stopped(claim),native_png=True)
  terminal=TrustedProcessEvidence(bridge,self.profiles)
  composed=self._compose(bridge)
  # No fallback: this adapter can prove closure of its exact native root only.
  # Existing OneStop caller handles legacy and discarded/root replay separately.
  ports=bind_native_failure(bridge,{'failure_guard_reader':no_work},composed.host_for,enabled=True)
  bridge.failure_fence=FailureFence(bridge,ports['failure_guard_reader'],operation_scope=ports['failure_operation_scope'])
  return composed.adapter(children_root)
