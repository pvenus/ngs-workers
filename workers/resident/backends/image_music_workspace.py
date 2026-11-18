"""PDS execution envelope shared by image/music ports; never a source DB/path reader."""
import hashlib,json,os
from pathlib import Path

def digest(value):
 return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def safe_file(root,name):
 relative=Path(name)
 if relative.is_absolute() or not relative.parts or any(x in ('..','.')for x in relative.parts):raise ValueError('WORKSPACE_RELATIVE_PATH_REQUIRED')
 path=root/relative
 if any(p.is_symlink()for p in (path,*path.parents)):raise ValueError('WORKSPACE_SYMLINK_DENIED')
 if not path.resolve(strict=True).is_relative_to(root) or not path.is_file():raise ValueError('WORKSPACE_FILE_REQUIRED')
 return path

def stage(claim,workspace):
 path=Path(workspace)
 if not path.is_absolute()or any(p.is_symlink()for p in (path,*path.parents)):raise ValueError('WORKSPACE_SYMLINK_DENIED')
 root=path.resolve(strict=True)
 packet=safe_file(root,'execution.json')
 if packet.stat().st_size>1024*1024:raise ValueError('EXECUTION_ENVELOPE_TOO_LARGE')
 value=json.loads(packet.read_bytes())
 expected={'schemaVersion':'workers.execution.v1','requestId':claim.request_id,'attemptId':claim.attempt_id,'capability':claim.capability,'expectedRevision':claim.expected_revision,'requestHash':digest(dict(claim.parameters)),'inputRefs':list(claim.input_refs)}
 if any(value.get(k)!=v for k,v in expected.items()):raise ValueError('EXECUTION_CLAIM_BINDING_MISMATCH')
 files=value.get('inputFiles',[])
 if not isinstance(files,list)or len(files)!=len(claim.input_refs)or [x.get('ref')for x in files]!=list(claim.input_refs):raise ValueError('EXECUTION_INPUT_REFS_MISMATCH')
 paths=[]
 for row in files:
  path=safe_file(root,row['path'])
  if hashlib.sha256(path.read_bytes()).hexdigest()!=row.get('sha256'):raise ValueError('EXECUTION_INPUT_HASH_MISMATCH')
  paths.append(path)
 return root,value,tuple(paths)

def output_directory(root):
 target=root/'artifacts'
 if target.exists()or target.is_symlink():raise ValueError('EXECUTION_OUTPUT_ALREADY_EXISTS')
 target.mkdir(mode=0o700)
 return target

def write_new(path,raw):
 fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 with os.fdopen(fd,'wb')as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
