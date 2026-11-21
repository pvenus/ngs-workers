"""Opt-in local boot-boundary observation/approval ports. Never controls launchd."""
import hashlib,json,os,plistlib,re,stat,subprocess,time
from pathlib import Path
from ..adapters.host_boundary_recovery import HostBoundaryRecovery,HostObservation,require,digest
from ..terminal_evidence import SourcePin

def owned_read(path,private=False):
 p=Path(path);require(p.is_absolute()and p.resolve()==p and not p.is_symlink(),'HOST_RECOVERY_FILE_PATH')
 with os.fdopen(os.open(p,os.O_RDONLY|os.O_NOFOLLOW),'rb')as f:
  s=os.fstat(f.fileno());require(stat.S_ISREG(s.st_mode)and s.st_uid==os.getuid()and not s.st_mode&(0o077 if private else 0o022),'HOST_RECOVERY_FILE_OWNER');return f.read()
class MacHostObserver:
 def __init__(self,*,worker_id,ops_config,desired_file,runner=subprocess.run,clock=time.time):
  self.worker_id=worker_id;self.ops_config=ops_config;self.desired_file=desired_file;self.runner=runner;self.clock=clock
 def run(self,args):return self.runner(args,capture_output=True,text=True,timeout=5)
 def __call__(self):
  before=self.clock();config=json.loads(owned_read(self.ops_config));rows=[x for x in config['registeredResidents']if x.get('id')=='document'];require(len(rows)==1,'HOST_RECOVERY_DOCUMENT_REGISTRATION');spec=rows[0]
  label='com.vinus.resident.document';target='gui/'+str(os.getuid())+'/'+label;require(spec['label']==label and spec['plistPath']==str(Path.home()/'Library/LaunchAgents'/(label+'.plist')),'HOST_RECOVERY_DOCUMENT_LABEL')
  raw=owned_read(spec['plistPath']);require(hashlib.sha256(raw).hexdigest()==spec['plistSha256'],'HOST_RECOVERY_PLIST_CHANGED');p=plistlib.loads(raw);require(p.get('Label')==label and p.get('ProgramArguments')==spec['programArguments'],'HOST_RECOVERY_PLIST_BINDING')
  desired=json.loads(owned_read(self.desired_file));require(isinstance(desired,dict)and all(type(v)is bool for v in desired.values()),'HOST_RECOVERY_DESIRED_STATE')
  machine=self.run(['/usr/sbin/ioreg','-rd1','-c','IOPlatformExpertDevice']);require(machine.returncode==0,'HOST_RECOVERY_MACHINE_UNKNOWN');m=re.search(r'"IOPlatformUUID"\s*=\s*"([A-Fa-f0-9-]{36})"',machine.stdout);require(m is not None,'HOST_RECOVERY_MACHINE_UNKNOWN');host=hashlib.sha256(m[1].lower().encode()).hexdigest()
  boot=self.run(['/usr/sbin/sysctl','-n','kern.boottime']);require(boot.returncode==0,'HOST_RECOVERY_BOOT_UNKNOWN');b=re.fullmatch(r'\{ sec = (\d+), usec = (\d+) \}.*',boot.stdout.strip());require(b is not None,'HOST_RECOVERY_BOOT_UNKNOWN');stamp=int(b[1])+int(b[2])/1e6;boot_id=digest({'sec':int(b[1]),'usec':int(b[2])})
  job=self.run(['/bin/launchctl','print',target]);stopped=job.returncode!=0 and 'Could not find service' in (job.stderr or '')
  disabled=self.run(['/bin/launchctl','print-disabled','gui/'+str(os.getuid())]);require(disabled.returncode==0,'HOST_RECOVERY_DISABLE_UNKNOWN');off=re.search(r'"'+re.escape(label)+r'"\s*=>\s*(?:true|disabled)(?=\s|$)' ,disabled.stdout)is not None
  require(self.clock()-before<=5,'HOST_RECOVERY_OBSERVATION_STALE')
  return HostObservation(host,boot_id,stamp,self.clock(),self.worker_id,stopped,desired.get('document')is False and off)
class ApprovalReader:
 def __init__(self,path,clock=time.time):self.path,self.clock=path,clock
 def __call__(self,c):
  a=json.loads(owned_read(self.path,True));require(set(a)=={'schemaVersion','action','requestId','scopeHash','approvalId','approved','issuedAt','expiresAt'}and a['schemaVersion']=='resident.host-recovery-approval.v1'and type(a['issuedAt'])in(int,float)and type(a['expiresAt'])in(int,float)and a['issuedAt']<=self.clock()<=a['expiresAt'],'HOST_RECOVERY_APPROVAL_INVALID');return a

def configured(bridge,spec):
 require(isinstance(spec,dict)and set(spec)=={'schemaVersion','enabled','hostId','workerId','sourcePins','opsConfig','desiredStateFile','approvalFile','serviceTokenFile'}and spec['schemaVersion']=='resident.host-recovery-config.v1'and spec['enabled']is True,'HOST_RECOVERY_CONFIG')
 pins=tuple(SourcePin(**p)for p in spec['sourcePins']);token=owned_read(spec['serviceTokenFile'],True).decode().strip();require(len(token)>=32,'HOST_RECOVERY_TOKEN')
 authority=HostBoundaryRecovery(bridge,host_id=spec['hostId'],worker_id=spec['workerId'],pins=pins,observe=MacHostObserver(worker_id=spec['workerId'],ops_config=spec['opsConfig'],desired_file=spec['desiredStateFile']),approval=ApprovalReader(spec['approvalFile']))
 return authority,token
