"""Cancellation-aware daemon bridge for the existing pinned native image runner.

The runner/verifier is selected by trusted deployment code, never request data.
No retries, source lookup, generation on import, or direct publication.
"""
import json,os,signal,subprocess,time
from pathlib import Path
from ..contracts import ExecutionStopUnconfirmed
from .image_music_workspace import write_new

class NativeImageCLI:
 def __init__(self,binary,workspace,folder,stop,*,grace=2):
  self.binary=Path(binary).resolve(strict=True);self.workspace=Path(workspace).resolve(strict=True)
  self.process_evidence=None;self.local_process_evidence=None;self.folder=Path(folder);self.folder.mkdir(mode=0o700);self.stop=stop;self.grace=grace
  if not self.binary.is_file()or self.folder.resolve(strict=True).parent!=self.workspace:raise ValueError('IMAGE_CLI_SCOPE_INVALID')
 def _join_group(self,process):
  for sig in (signal.SIGTERM,signal.SIGKILL):
   try:os.killpg(process.pid,sig)
   except ProcessLookupError:pass
   try:process.wait(timeout=self.grace)
   except subprocess.TimeoutExpired:continue
   until=time.monotonic()+self.grace
   while time.monotonic()<until:
    try:os.killpg(process.pid,0)
    except ProcessLookupError:return
    time.sleep(.02)
  raise ExecutionStopUnconfirmed('IMAGE_PROCESS_EXIT_UNCONFIRMED')
 def run(self,prompt,schema,name,timeout=900,mcp_args=None,minimal=False):
  self.process_evidence=None;self.local_process_evidence=None
  remote_unknown=False
  if mcp_args or not minimal or not isinstance(name,str)or not name.startswith('node.image.')or '/'in name or '..'in name:raise ValueError('IMAGE_CLI_CALL_INVALID')
  if self.stop.is_set():raise RuntimeError('LEASE_LOST')
  root=self.folder/name;root.mkdir(mode=0o700)
  write_new(root/'response-schema.json',json.dumps(schema).encode())
  args=[str(self.binary),'--no-daemon','exec','--ephemeral','--sandbox','read-only','--skip-git-repo-check','--cd',str(self.workspace),'--json','--output-schema',str(root/'response-schema.json'),'--output-last-message',str(root/'response.json'),'--disable','apps','-']
  with (root/'events.jsonl').open('xb')as out,(root/'stderr.log').open('xb')as err:
   process=subprocess.Popen(args,stdin=subprocess.PIPE,stdout=out,stderr=err,start_new_session=True,env={**os.environ,'RUST_LOG':'error,codex_otel.trace_safe=info,codex_otel.log_only=info'})
   try:
    # communicate handles stdin backpressure without blocking cancellation.
    deadline=time.monotonic()+min(timeout,900);pending=prompt.encode()
    while True:
     try:process.communicate(input=pending,timeout=.1);break
     except subprocess.TimeoutExpired:pending=None
     if self.stop.is_set()or time.monotonic()>=deadline:
      self._join_group(process)
      remote_unknown=True
      raise ExecutionStopUnconfirmed('IMAGE_REMOTE_OUTCOME_RECONCILIATION_REQUIRED')
    # Children may outlive a successful CLI leader. Stop and verify that group too.
    self._join_group(process)
   except BaseException:
    self._join_group(process);raise
   finally:
    # Persist OS observation before parsing or provider output validation.
    # This is local termination evidence, not remote publication settlement.
    if process.poll() is not None:
     try:os.killpg(process.pid,0)
     except ProcessLookupError:
      self.process_evidence={'kind':'local-processes','processGroups':[process.pid],'outcome':'joined','returnCodes':[process.returncode]}
      self.local_process_evidence=dict(self.process_evidence)
      write_new(root/'process-exit.json',json.dumps(self.local_process_evidence,sort_keys=True).encode())
      if remote_unknown:self.process_evidence=None
  path=root/'response.json'
  if process.returncode or path.is_symlink()or not path.is_file()or path.stat().st_size>60000:raise ExecutionStopUnconfirmed('IMAGE_NATIVE_RESULT_UNCONFIRMED')
  result=json.loads(path.read_text())
  self.process_evidence={'kind':'local-processes','processGroups':[process.pid],'outcome':'joined','returnCodes':[process.returncode]}
  return result,root/'events.jsonl'

class NativeImageProvider:
 def __init__(self,workspace,*,binary,runner_builder):
  self.workspace=Path(workspace);self.binary=binary;self.runner_builder=runner_builder;self.process_evidence=None
 def run(self,native_claim,workspace,cancel):
  if Path(workspace)!=self.workspace:raise ValueError('IMAGE_PROVIDER_WORKSPACE_MISMATCH')
  self.process_evidence=None
  client=NativeImageCLI(self.binary,self.workspace,self.workspace/'native-cli',cancel)
  runner=self.runner_builder(client,self.workspace)
  # Existing checked/handoff/actual-argument/native-call/output verification and
  # durable started marker stay owned by the original runner.
  try:return runner.generate(native_claim)
  finally:self.process_evidence=client.process_evidence
