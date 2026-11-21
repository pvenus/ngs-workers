"""Reviewed NGS source contracts without constructing operating services.

Opening a writer is a separate, explicit action. No default operational root,
Common schema initialization, parent constructor or network call on import/load.
"""
import hashlib,importlib,sys
from dataclasses import dataclass
from pathlib import Path

MODULES={
 'common':'local.services.request_pipeline.common',
 'category_dispatch':'local.services.request_pipeline.category_dispatch',
 'category_executor':'local.services.npc_workflow.category_single',
 'parent':'local.services.npc_workflow.single_batch',
 'parent_host':'local.services.npc_workflow.single_host',
 'ownership':'local.services.npc_ownership',
 'animation_contract':'local.services.animation_requests.contract',
 'animation_service':'local.services.animation_requests.service',
 'animation_batch':'local.services.animation_requests.batch',
 'pose_service':'local.services.pose_requests.service',
 'pose_worker':'local.services.pose_requests.worker',
 'pose_contract':'local.services.pose_requests.contract',
 'frame_contract':'local.services.frame_requests.contract',
 'frame_service':'local.services.frame_requests.service',
 'one_stop_service':'local.services.npc_one_stop.service',
 'one_stop_adapters':'local.services.npc_one_stop.adapters',
 'one_stop_plan':'local.services.npc_one_stop.plan',
 'one_stop_retry':'local.services.npc_one_stop.retry_request',
}

@dataclass(frozen=True)
class NGSSourceTypes:
 modules:dict
 def open_common_existing(self,path,expected_selection):
  if not isinstance(expected_selection,dict) or not expected_selection:
   raise ValueError('NGS_DATABASE_SELECTION_REQUIRED')
  # Existing CommonRequests performs owner/inode/schema/selector checks itself.
  # Caller creates one authority per thread; SQLite check_same_thread stays on.
  return self.modules['common'].CommonRequests(path,existing_schema_only=True,expected_selection=expected_selection)
 @property
 def category_dispatch(self):return self.modules['category_dispatch'].CategoryDispatch
 @property
 def session_type(self):return self.modules['ownership'].Session


def load_source_types(root,manifest):
 root=Path(root)
 if not root.is_absolute() or root.is_symlink() or not root.is_dir() or root.resolve()!=root:
  raise ValueError('NGS_SOURCE_ROOT_INVALID')
 if (root/'local').is_symlink() or any(p.is_symlink() for p in (root/'local').rglob('*')):
  raise ValueError('NGS_SOURCE_SYMLINK_DENIED')
 # Pin all Python dependency files under local, not only the visible entrypoint.
 observed={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
           for p in (root/'local').rglob('*.py') if p.is_file() and not p.is_symlink()}
 if not isinstance(manifest,dict) or not observed or observed!=manifest:
  raise ValueError('NGS_SOURCE_MANIFEST_MISMATCH')
 for name,module in tuple(sys.modules.items()):
  if name=='local' or name.startswith('local.'):
   file=getattr(module,'__file__',None)
   paths=getattr(module,'__path__',())
   if file and not Path(file).resolve().is_relative_to(root) or any(not Path(p).resolve().is_relative_to(root) for p in paths):
    raise ValueError('NGS_SOURCE_MODULE_COLLISION')
 sys.path.insert(0,str(root))
 try:
  loaded={key:importlib.import_module(name) for key,name in MODULES.items()}
  if any(not Path(m.__file__).resolve().is_relative_to(root) for m in loaded.values()):
   raise ValueError('NGS_SOURCE_MODULE_COLLISION')
  return NGSSourceTypes(loaded)
 finally:sys.path.remove(str(root))
