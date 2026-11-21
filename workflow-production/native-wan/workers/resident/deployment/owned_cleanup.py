"""Delete only the exact local claim workspace after caller's terminal proof."""
import json,os,shutil
from pathlib import Path
from dataclasses import asdict
from .request_selector import digest
from ..adapters.attempt_directory_lock import directory_lock

def cleanup_factory(roots):
 roots=tuple(Path(p)for p in roots)
 if not roots or any(not p.is_absolute()or p.is_symlink()or p.resolve(strict=True)!=p or not p.is_dir()for p in roots):
  raise ValueError('CLEANUP_MANAGED_ROOTS_REQUIRED')
 def cleanup(claim,folder):
  folder=Path(folder);expected=digest([claim.request_id,claim.attempt_id])
  if folder.name!=expected or folder.parent not in roots or folder.is_symlink():return False
  with directory_lock(folder,exclusive=True) as exists:
   if not exists:return True
   if not folder.is_dir()or folder.stat().st_uid!=os.getuid():return False
   marker=folder/'claim.json'
   if marker.is_symlink()or not marker.is_file():return False
   if json.loads(marker.read_text())!=json.loads(json.dumps(asdict(claim))):return False
   # Refuse unexpected links; never traverse an original/PDS tree through scratch.
   if any(p.is_symlink()for p in folder.rglob('*')):return False
   shutil.rmtree(folder)
   return not folder.exists()
 return cleanup
