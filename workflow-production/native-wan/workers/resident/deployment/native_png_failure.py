"""Exact native host ports for the existing Request FailureFence.

No new fence, migration, permission or backfill. Use the returned domain before
the ordinary host builder creates its FailureFence and initializes its schema.
The trusted host factory resolves an already persisted original Session.
"""
from .resident_operation_proof import OperationScope

REQUEST='animation.native-sequence'
SCHEMA='ngs.animation-native-sequence-batch.v1'
SOURCE='npc.animation.native-batch'

def bind_native_failure(bridge,domain,host_for,*,enabled=False):
 if type(enabled)is not bool:raise ValueError('NATIVE_PNG_OPT_IN_INVALID')
 if not enabled:return domain
 if not callable(host_for)or not callable(domain.get('failure_guard_reader')):
  raise ValueError('NATIVE_FAILURE_HOST_REQUIRED')
 old_guard=domain['failure_guard_reader'];old_scope=domain.get('failure_operation_scope')
 if old_scope is not None and not callable(old_scope):raise ValueError('NATIVE_FAILURE_HOST_REQUIRED')
 common=bridge.common
 def native(rid):
  row=common.row(rid)
  parts=(row['request_type'],row['schema_version'],row['source'])
  if not (rid.startswith('animation.native.')or parts[2]==SOURCE):return None
  if (not rid.startswith('animation.native.')or parts!=(REQUEST,SCHEMA,SOURCE)or row['parent_id'] is not None or row['request_id']!=rid):
   raise ValueError('NATIVE_FAILURE_PARENT_SCOPE_CHANGED')
  host=host_for(rid,row['requester'])
  if host.common is not common or host.session.rid!=rid:raise ValueError('NATIVE_FAILURE_HOST_SCOPE_CHANGED')
  return host
 def guard(rid):
  host=native(rid)
  return host._original_guard(rid) if host is not None else old_guard(rid)
 def scope(g):
  rid=g['requestId'];host=native(rid)
  if host is None:return old_scope(g) if old_scope is not None else None
  if host._original_guard(rid)!=g:raise ValueError('NATIVE_FAILURE_GUARD_CHANGED')
  ids,binding=host.failure_scope(g)
  return OperationScope(g,ids,binding)
 return {**domain,'failure_guard_reader':guard,'failure_operation_scope':scope}

def attach_native_failure_reader(host,bridge):
 """Attach only the existing configured fence on this same Request authority."""
 fence=getattr(bridge,'failure_fence',None)
 if host.common is not bridge.common or fence is None or fence.bridge is not bridge:
  raise ValueError('NATIVE_FAILURE_FENCE_REQUIRED')
 host.failure_reader=fence.failure_reader
 return host
