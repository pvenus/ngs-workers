"""Read-only current animation quiescence, never historical execution evidence."""
import errno,hashlib,json,os,plistlib,re,socket,subprocess,time
from pathlib import Path
from .host_recovery_site import owned_read
from ..adapters.animation_timeout_disposition import Quiescence


def need(ok):
    if not ok:raise ValueError('ANIMATION_QUIESCENCE_UNVERIFIED')


class MacAnimationQuiescence:
    def __init__(self, *, ops_config, desired_file, comfy_root, scratch_root,
                 residency_root, worker_id='resident-animation', runner=subprocess.run,
                 clock=time.time, connection_probe=None):
        self.ops_config,self.desired_file=ops_config,desired_file
        self.roots=tuple(str(Path(p)) for p in (comfy_root,scratch_root,residency_root))
        need(all(Path(p).is_absolute() and p!='/' for p in self.roots))
        need(worker_id=='resident-animation')
        self.worker_id,self.runner,self.clock=worker_id,runner,clock
        self.connection_probe=connection_probe or self._port
    def run(self,args):
        return self.runner(args,capture_output=True,text=True,timeout=5)
    def _service(self):
        config=owned_read(self.ops_config);desired=owned_read(self.desired_file)
        rows=[r for r in json.loads(config)['registeredResidents'] if r.get('id')=='animation']
        need(len(rows)==1);r=rows[0];label='com.vinus.resident.animation'
        need(r['label']==label and r['plistPath']==str(Path.home()/'Library/LaunchAgents'/(label+'.plist')))
        raw=owned_read(r['plistPath']);p=plistlib.loads(raw)
        need(hashlib.sha256(raw).hexdigest()==r['plistSha256'] and p.get('Label')==label and p.get('ProgramArguments')==r['programArguments'])
        args=p['ProgramArguments'];need(any(args[i:i+3]==['-m','workers.resident','animation'] for i in range(len(args))))
        state=json.loads(desired);need(state.get('animation') is False)
        q=self.run(['/bin/launchctl','print','gui/'+str(os.getuid())+'/'+label])
        need(q.returncode==113 and 'Could not find service' in q.stderr)
        return hashlib.sha256(config+desired+raw).hexdigest()
    @staticmethod
    def _port():
        with socket.socket() as s:
            s.settimeout(1);result=s.connect_ex(('127.0.0.1',8192))
        need(result in (0,errno.ECONNREFUSED))
        return result==0
    def _processes(self,engine=None):
        q=self.run(['/bin/ps','-ww','-axo','pid=,ppid=,pgid=,command='])
        need(q.returncode==0 and not q.stderr.strip() and bool(q.stdout.strip()))
        rows={}
        for line in q.stdout.splitlines():
            m=re.fullmatch(r'\s*(\d+)\s+(\d+)\s+(\d+)\s+(.+)',line)
            need(m is not None)
            pid,ppid,pgid=(int(m[i]) for i in (1,2,3));need(pid not in rows)
            need(m[4]!='?');rows[pid]=(ppid,pgid,m[4])
        need(1 in rows and os.getpid() in rows)
        if engine is not None:
            need(not any(pid==engine['pid'] or pgid==engine['pgid'] for pid,(_,pgid,_) in rows.items()))
        # Exclude only this observer's command ancestry, not its descendants.
        excluded=set();pid=os.getpid()
        while pid in rows and pid not in excluded:
            excluded.add(pid);pid=rows[pid][0]
        matches=set()
        for pid,(ppid,pgid,command) in rows.items():
            if pid in excluded:continue
            if (re.search(r'workers\.resident\s+animation(?:\s|$)',command)
                or '/animation_assets/bootstrap.py' in command
                or any(root in command for root in self.roots)
                or re.search(r'(?:^|/)(?:ffmpeg|ffprobe)(?:\s|$)',command)
                or ('multiprocessing' in command and any(x in command for x in ('spawn_main','forkserver','resource_tracker')))):
                matches.add(pid)
        while True:
            extra={pid for pid,(ppid,_,_) in rows.items() if ppid in matches}
            if extra<=matches:break
            matches|=extra
        # IDs only; never persist raw argv, prompts, file names, or secrets.
        return tuple(sorted(matches))
    def __call__(self,claim,*,engine=None):
        need(claim.capability=='animation.generate');start=self.clock();first=self._service()
        q=self.run(['/usr/sbin/ioreg','-rd1','-c','IOPlatformExpertDevice'])
        m=re.search(r'"IOPlatformUUID"\s*=\s*"([A-Fa-f0-9-]{36})"',q.stdout)
        need(q.returncode==0 and m is not None);host=hashlib.sha256(m[1].lower().encode()).hexdigest()
        q=self.run(['/usr/sbin/sysctl','-n','kern.boottime'])
        m=re.fullmatch(r'\{ sec = (\d+), usec = (\d+) \}.*',q.stdout.strip())
        need(q.returncode==0 and m is not None)
        boot=hashlib.sha256((m[1]+':'+m[2]).encode()).hexdigest()
        processes=self._processes(engine);listening=self.connection_probe()
        need(type(listening)is bool);need(first==self._service())
        now=self.clock();need(0<=now-start<=5)
        return Quiescence(host,boot,first,self.worker_id,now,True,False,True,processes,listening)
