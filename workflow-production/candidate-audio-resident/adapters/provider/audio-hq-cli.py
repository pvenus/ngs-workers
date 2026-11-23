"""Host launcher: approved JSON from stdin, one structured WAV receipt on stdout."""
import json
from contextlib import ExitStack
import os
import select
import re
from pathlib import Path
import signal
import sys
import threading
sys.path.insert(0,str(Path(__file__).resolve().parent))
from hq_bgm import render_hq, render_cover_hq

MAX_WIRE = 32*1024**2 + 512

def dispatch(value,*args,**kwargs):
    if isinstance(value,dict) and value.get('kind')=='separate':
        return render_cover_hq(value,*args,**kwargs)
    return render_hq(value,*args,**kwargs)

from music_boundary import _music_boundary_from_binding,HQ_PROFILE

def main():
    if len(sys.argv) not in (2,3) or len(sys.argv)==3 and sys.argv[2]!='--resident':
        raise ValueError('AUDIO_HQ_CONFIGURATION')
    stopped=threading.Event()
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,lambda *_:stopped.set())
    if len(sys.argv)==3:
        return resident_main(sys.argv[1],stopped)
    data=sys.stdin.buffer.read(MAX_WIRE+1)
    if len(data)>MAX_WIRE:raise ValueError('AUDIO_HQ_INPUT_OVERSIZE')
    result=dispatch(json.loads(data),sys.argv[1],stopped.is_set)
    if stopped.is_set():raise ValueError('AUDIO_HQ_CANCELLED')
    print(json.dumps(result,separators=(',',':'),allow_nan=False))

def resident_frames(fd,stopped):
    cap=MAX_WIRE+64
    buf=bytearray()
    searched=0
    while not stopped.is_set():
        end=buf.find(b'\n',searched)
        searched=len(buf)
        if end>=cap or end<0 and len(buf)>=cap:
            raise ValueError('AUDIO_HQ_INPUT_OVERSIZE')
        if end>=0:
            data=bytes(buf[:end]);del buf[:end+1]
            searched=0
            yield data
            continue
        readable,_,_=select.select([fd],[],[],.5)
        if readable:
            more=os.read(fd,min(65536,cap-len(buf)))
            if not more:
                return
            buf.extend(more)


def resident_main(binding,stopped, frames=resident_frames, connect=_music_boundary_from_binding, render=dispatch):
    from types import SimpleNamespace
    last=0
    connector=None
    with ExitStack() as cleanup:
        for line in frames(sys.stdin.fileno(), stopped):
            value=json.loads(line)
            if (type(value) is not dict or set(value)!={'id','input'} or
                    type(value['id']) is not str or not re.fullmatch(r'[1-9][0-9]{0,19}',value['id']) or int(value['id'])<=last):
                raise ValueError('AUDIO_HQ_INPUT_INVALID')
            token=value['id'];last=int(token)
            try:
                if connector is None:
                    module,config=connect(HQ_PROFILE,binding)
                    generate=cleanup.enter_context(module.resident_music(config))
                    provider=SimpleNamespace(generate_music=generate)
                    connector=lambda *_:(provider,config)
                outcome=render(value['input'],binding,stopped.is_set,connect=connector)
                if stopped.is_set():
                    return
                reply={'id':token,'ok':True,'result':outcome}
            except Exception as exc:
                if stopped.is_set():
                    return
                phase,error=diagnostic(exc)
                reply={'id':token,'ok':False,'error':error,'phase':phase}
            print(json.dumps(reply,separators=(',',':'),allow_nan=False),flush=True)
            if not reply['ok']:
                # One failed command; do not retry using a possibly tainted model.
                return


# Outward diagnostics are enumerable. Never print arbitrary exception messages.
_STAGE_CODES = frozenset((
    'HQ_LM_AUTO_DURATION_INVALID', 'HQ_LM_AUTO_DURATION_MISSING', 'HQ_LM_AUTO_DURATION_MALFORMED',
    'HQ_LM_AUTO_DURATION_OUT_OF_RANGE', 'HQ_LM_FULL_DURATION_NOT_MET', 'AUDIO_HQ_FULL_DURATION_NOT_MET', 'HQ_LM_AUTO_DURATION_CAP_UNAVAILABLE', 'HQ_LM_METADATA_INVALID', 'HQ_LM_GENERATION_FAILED',
    'HQ_LM_AUDIO_CODES_INVALID', 'HQ_LM_AUDIO_CODE_RANGE_INVALID', 'HQ_BPM_INVALID',
    'HQ_TIME_SIGNATURE_INVALID', 'HQ_SETTING_TEXT_INVALID', 'HQ_LM_MLX_INITIALIZATION_REQUIRED',
    'HQ_SEPARATOR_UNPROVISIONED', 'HQ_COVER_SOURCE_INVALID', 'HQ_COVER_ACCOMPANIMENT_EMPTY', 'HQ_MPS_REQUIRED', 'HQ_MPS_FP32_RENDERER_REQUIRED', 'HQ_REQUIRED_COMPONENT_MISSING',
    'HQ_RENDER_GENERATION_FAILED', 'HQ_GENERATED_OUTPUT_PATH_INVALID', 'HQ_WAV_FORMAT_INVALID',
    'HQ_WAV_DURATION_INVALID', 'HQ_PLANNER_BINDING_MISMATCH', 'HQ_REQUEST_HASH_MISMATCH',
    'HQ_CHILD_OUT_OF_MEMORY', 'HQ_CHILD_NONZERO_EXIT', 'HQ_CHILD_SIGNAL', 'HQ_PIPELINE_TIMEOUT',
    'HQ_MEMORY_PRESSURE_STOP', 'HQ_MEMORY_MONITOR_UNAVAILABLE', 'HQ_DISK_LOW_STOP',
    'HQ_RESIDENT_CHILD_EXITED','HQ_RESIDENT_REPLY_INVALID','HQ_RESIDENT_JOB_BINDING','HQ_RESIDENT_REQUEST_FRAME',
))
_STAGE_DIRECT = {
    'AUDIO_HQ_CONFIGURATION':'setup', 'AUDIO_HQ_INPUT_OVERSIZE':'setup',
    'AUDIO_HQ_INPUT_INVALID':'setup', 'AUDIO_HQ_BINDING_INVALID':'setup',
    'AUDIO_HQ_SCRATCH_INVALID':'setup', 'AUDIO_HQ_UNAVAILABLE':'setup',
    'BOUNDARY_SHARED_BUSY_NO_RETRY':'setup', 'BOUNDARY_LEGACY_BUSY_NO_RETRY':'setup',
    'HQ_MEMORY_UNSAFE_BEFORE_START':'setup', 'HQ_DISK_LOW_STOP':'setup',
    'HQ_LM_AUTO_DURATION_INVALID':'plan', 'HQ_LM_AUTO_DURATION_MISSING':'plan',
    'HQ_LM_AUTO_DURATION_MALFORMED':'plan', 'HQ_LM_AUTO_DURATION_OUT_OF_RANGE':'plan',
    'HQ_LM_AUTO_DURATION_CAP_UNAVAILABLE':'plan', 'HQ_LM_FULL_DURATION_NOT_MET':'plan',
    'AUDIO_HQ_FULL_DURATION_NOT_MET':'verify', 'HQ_LM_METADATA_INVALID':'plan',
    'HQ_WAV_INVALID':'verify', 'HQ_AUDIO_RECEIPT_MISMATCH':'verify',
    'AUDIO_HQ_WAV_FORMAT':'verify', 'AUDIO_HQ_WAV_DURATION':'verify',
    'AUDIO_HQ_AUTO_DURATION_PROOF_MISMATCH':'verify', 'AUDIO_HQ_HUMMING_PROOF_MISMATCH':'verify',
    'HQ_SEPARATOR_UNPROVISIONED':'setup', 'HQ_COVER_SOURCE_INVALID':'setup',
    'AUDIO_HQ_COVER_PROOF_INVALID':'verify', 'AUDIO_HQ_PROVIDER_PROOF':'verify', 'AUDIO_HQ_TEMPO_PROOF_MISMATCH':'verify',
    'AUDIO_HQ_PROOF_SCOPE':'verify', 'AUDIO_HQ_FILE_INVALID':'verify', 'AUDIO_HQ_FILE_CHANGED':'verify',
    'AUDIO_HQ_CANCELLED':'unknown',
}

def diagnostic(exc):
    msg=str(exc)
    found=re.fullmatch(r'HQ_STAGE_DIAGNOSTIC:(plan|render):([A-Z0-9_]{2,63})',msg)
    if found and found[2] in _STAGE_CODES:
        return found[1],found[2]
    if msg.startswith('HQ_PLAN_BINDING_MISMATCH:'):
        return 'plan','HQ_PLAN_BINDING_MISMATCH'
    if msg.startswith('HQ_RENDER_BINDING_MISMATCH:'):
        return 'verify','HQ_RENDER_BINDING_MISMATCH'
    if msg in _STAGE_DIRECT:
        # Boundary internals have a separate prefix; expose only a generic trusted code.
        return _STAGE_DIRECT[msg],('HQ_BOUNDARY_BUSY' if msg.startswith('BOUNDARY_') else msg)
    if msg.startswith(('AUDIO_WORKERS_','HQ_BRIDGE_','BOUNDARY_')):
        return 'setup','HQ_PROVIDER_BINDING_FAILED'
    if isinstance(exc,FileNotFoundError):
        return 'unknown','HQ_FILE_MISSING'
    if isinstance(exc,PermissionError):
        return 'unknown','HQ_PERMISSION_DENIED'
    return 'unknown','HQ_UNCLASSIFIED'

if __name__=='__main__':
    try:
        main()
    except Exception as exc:
        stage,code=diagnostic(exc)
        print(f'AUDIO_HQ_DIAGNOSTIC stage={stage} code={code}',file=sys.stderr,flush=True)
        raise SystemExit(1) from None
