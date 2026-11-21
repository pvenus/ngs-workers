"""Coordinate exact existing attempt reads and cleanup on the directory inode.

No lock files, global locks, directory creation or ownership exemptions. Missing
paths retain their caller's existing absent-allocation meaning, never stop proof.
"""
import fcntl,os,stat
from pathlib import Path
from contextlib import contextmanager,ExitStack

def checked(path):
 p=Path(path)
 if not p.is_absolute() or p.resolve()!=p or any(x.is_symlink() for x in (p,*p.parents)):
  raise ValueError('ATTEMPT_DIRECTORY_UNSAFE')
 return p

@contextmanager
def directory_lock(path,*,exclusive=False):
 p=checked(path)
 try:fd=os.open(p,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
 except FileNotFoundError:
  # Recheck symlinks even on a disappearing/absent path.
  checked(p)
  yield False
  return
 try:
  fcntl.flock(fd,fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
  opened=os.fstat(fd);checked(p)
  try:current=p.stat()
  except FileNotFoundError:
   yield False # a previous exclusive cleanup finished while we waited
   return
  if not stat.S_ISDIR(opened.st_mode) or (opened.st_dev,opened.st_ino)!=(current.st_dev,current.st_ino):
   raise ValueError('ATTEMPT_DIRECTORY_REPLACED')
  yield True
 finally:
  os.close(fd)

@contextmanager
def directory_read_cohort(paths):
 # Deterministic ordering for independent parent observers. Cleanup locks one
 # attempt, never the enclosing root or another attempt.
 with ExitStack() as stack:
  for path in sorted({str(Path(p))for p in paths}):stack.enter_context(directory_lock(path))
  yield
