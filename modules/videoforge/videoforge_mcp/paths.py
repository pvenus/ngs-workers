from __future__ import annotations
from pathlib import Path

def confined(path:Path,root:Path,must_exist:bool=False)->Path:
 root=root.expanduser().resolve(strict=True)
 resolved=path.expanduser().resolve(strict=must_exist)
 if not resolved.is_relative_to(root):raise ValueError('path_outside_allowlist')
 return resolved
