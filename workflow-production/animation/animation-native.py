"""Turn pinned, local Wan decoded frames into lossless PNG; never encode MP4."""
import hashlib
import json
import os
import re
import signal
import sys
import threading
from pathlib import Path

def emit(value):
    print(json.dumps(value,separators=(',',':')),flush=True)

def error_code(error,fallback='ANIMATION_NATIVE_FAILED'):
    message=str(error)
    return message if re.fullmatch(r'[A-Z0-9_]{1,130}',message) else fallback

def render(root,stop,session,run,Image,*,retained=False):
    job = json.loads((root/'job.json').read_bytes())
    count, fps, key = job['frameCount'], job['fps'], job['requestId']
    if (type(count) is not int or not 2 <= count <= 1000 or
            type(fps) not in (float, int) or not 0 < fps <= 120 or
            not re.fullmatch(r'workflow[.]task[.][0-9a-f]{64}', key)):
        raise ValueError('ANIMATION_TASK_INPUT')
    job['referencePath'] = str(root/'portrait.png')
    job['motionPath'] = str(root/'motion.mp4')
    job['outputMode'] = 'native-png'
    if retained:
        # Private per-attempt engine files; keep the original task-derived seed.
        job['seed']=int(hashlib.sha256(key.encode()).hexdigest()[:8],16)
        job['requestId']='resident.'+hashlib.sha256((key+'/'+root.name).encode()).hexdigest()[:48]
        session.terminal=None
    def decoded(result_root, _, __):
        names = [f'{i:05d}.png' for i in range(count)]
        hashes = []
        total = 0
        for name in names:
            if stop.is_set():
                raise ValueError('ANIMATION_CANCELLED')
            file = result_root/'frames'/name
            data = file.read_bytes()
            total += len(data)
            if len(data)>8*1024**2 or total>64*1024**2:
                raise ValueError('ANIMATION_PNG_TOO_LARGE')
            with Image.open(file) as im:
                if im.format!='PNG' or im.size!=(768,432):
                    raise ValueError('ANIMATION_PNG_FORMAT')
                im.verify()
            hashes.append(hashlib.sha256(data).hexdigest())
        return {'sha256': hashlib.sha256(''.join(hashes).encode()).hexdigest(),
                'width':768,'height':432,'fps':fps,'frames':names,'frameHashes':hashes}
    result=run(root/'wan',job,session,stop,decoded_output=decoded)
    if stop.is_set():raise ValueError('ANIMATION_CANCELLED')
    if retained:
        session.cleanup_job(job['requestId'],result['sha256'])
    return result

def serve(root,session,stop,run,Image,stdin=None):
    """Same-owner, single-command pipe. stdout is a bounded reply protocol."""
    stdin=sys.stdin.buffer if stdin is None else stdin
    attempted_stop=False
    def unload_once():
        nonlocal attempted_stop
        if attempted_stop:return False
        attempted_stop=True
        try:
            session.unload()
            return session.process is None
        except Exception:return False
    try:
        while True:
            raw=stdin.readline(257)
            if not raw:return
            if len(raw)>256 or not raw.endswith(b'\n'):return
            command=json.loads(raw)
            if command=={'op':'close'}:
                emit({'kind':'closed','joined':unload_once()})
                return
            if not isinstance(command,dict) or set(command)!={'op','job'} or command['op']!='render':
                return
            name=command['job']
            if not isinstance(name,str) or not re.fullmatch(r'job-[A-Za-z0-9]{6}',name):return
            job=root/name
            if job.is_symlink() or job.resolve(strict=True)!=job or not job.is_dir():return
            try:
                if stop.is_set():raise ValueError('ANIMATION_CANCELLED')
                result=render(job,stop,session,run,Image,retained=True)
            except Exception as primary:
                joined=unload_once()
                emit({'kind':'error','code':error_code(primary) if joined else 'ANIMATION_PROCESS_STOP_UNCONFIRMED','joined':joined})
                return  # no second stop, no more renders in this helper
            emit({'kind':'rendered','result':result})
    finally:
        if not attempted_stop:unload_once()

def main():
    provider,binding_path,control_dir=map(Path,sys.argv[1:4])
    mode=sys.argv[4:]
    sys.path.insert(0,str(provider.resolve(strict=True)))
    from workers.resident.backends.animation import binding,OwnedComfy
    from workers.resident.backends.animation_wan import run
    from PIL import Image
    config=binding(binding_path)
    if mode!=['--serve']:
        import psutil
        if psutil.virtual_memory().available < 48*2**30:
            raise ValueError('ANIMATION_MEMORY_NOT_READY')
    if not mode and str(control_dir)=='--probe':
        import socket
        try:sock=socket.create_connection(('127.0.0.1',8192),timeout=1)
        except ConnectionRefusedError:
            emit({'ready':True})
            return
        sock.close()
        raise ValueError('ANIMATION_COMFY_PORT_BUSY')
    if mode not in ([],['--serve']):raise ValueError('ANIMATION_NATIVE_MODE')
    root=control_dir.resolve(strict=True)
    stop=threading.Event()
    for sig in (signal.SIGTERM,signal.SIGINT):
        signal.signal(sig,lambda *_:stop.set())
    if mode==['--serve']:
        serve(root,OwnedComfy(config,root/'engine',retain_main_model=True),stop,run,Image)
        return
    session=OwnedComfy(config,root/'engine')
    try:
        try:result=render(root,stop,session,run,Image)
        finally:session.unload()
    except Exception as e:
        if session.process is None:
            print('JOINED '+error_code(e),file=sys.stderr)
            sys.exit(1)
        raise
    emit(result)

if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        message = str(e)
        print(message if re.fullmatch(r'[A-Z0-9_]{1,130}', message) else 'ANIMATION_NATIVE_UNCONFIRMED',file=sys.stderr)
        sys.exit(1)
