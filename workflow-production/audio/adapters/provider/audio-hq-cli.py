"""Host launcher: approved JSON from stdin, one structured WAV receipt on stdout."""
import json
import re
from pathlib import Path
import signal
import sys
import threading
sys.path.insert(0,str(Path(__file__).resolve().parent))
from hq_bgm import render_hq

def main():
    if len(sys.argv)!=2:raise ValueError('AUDIO_HQ_CONFIGURATION')
    stopped=threading.Event()
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,lambda *_:stopped.set())
    data=sys.stdin.buffer.read(32769)
    if len(data)>32768:raise ValueError('AUDIO_HQ_INPUT_OVERSIZE')
    result=render_hq(json.loads(data),sys.argv[1],stopped.is_set)
    if stopped.is_set():raise ValueError('AUDIO_HQ_CANCELLED')
    print(json.dumps(result,separators=(',',':'),allow_nan=False))

# Outward diagnostics are enumerable. Never print arbitrary exception messages.
_STAGE_CODES = frozenset((
    'HQ_LM_AUTO_DURATION_INVALID', 'HQ_LM_AUTO_DURATION_MISSING', 'HQ_LM_AUTO_DURATION_MALFORMED',
    'HQ_LM_AUTO_DURATION_OUT_OF_RANGE', 'HQ_LM_AUTO_DURATION_CAP_UNAVAILABLE', 'HQ_LM_METADATA_INVALID', 'HQ_LM_GENERATION_FAILED',
    'HQ_LM_AUDIO_CODES_INVALID', 'HQ_LM_AUDIO_CODE_RANGE_INVALID', 'HQ_BPM_INVALID',
    'HQ_TIME_SIGNATURE_INVALID', 'HQ_SETTING_TEXT_INVALID', 'HQ_LM_MLX_INITIALIZATION_REQUIRED',
    'HQ_MPS_REQUIRED', 'HQ_MPS_FP32_RENDERER_REQUIRED', 'HQ_REQUIRED_COMPONENT_MISSING',
    'HQ_RENDER_GENERATION_FAILED', 'HQ_GENERATED_OUTPUT_PATH_INVALID', 'HQ_WAV_FORMAT_INVALID',
    'HQ_WAV_DURATION_INVALID', 'HQ_PLANNER_BINDING_MISMATCH', 'HQ_REQUEST_HASH_MISMATCH',
    'HQ_CHILD_OUT_OF_MEMORY', 'HQ_CHILD_NONZERO_EXIT', 'HQ_CHILD_SIGNAL', 'HQ_PIPELINE_TIMEOUT',
    'HQ_MEMORY_PRESSURE_STOP', 'HQ_MEMORY_MONITOR_UNAVAILABLE', 'HQ_DISK_LOW_STOP',
))
_STAGE_DIRECT = {
    'AUDIO_HQ_CONFIGURATION':'setup', 'AUDIO_HQ_INPUT_OVERSIZE':'setup',
    'AUDIO_HQ_INPUT_INVALID':'setup', 'AUDIO_HQ_BINDING_INVALID':'setup',
    'AUDIO_HQ_SCRATCH_INVALID':'setup', 'AUDIO_HQ_UNAVAILABLE':'setup',
    'BOUNDARY_SHARED_BUSY_NO_RETRY':'setup', 'BOUNDARY_LEGACY_BUSY_NO_RETRY':'setup',
    'HQ_MEMORY_UNSAFE_BEFORE_START':'setup', 'HQ_DISK_LOW_STOP':'setup',
    'HQ_LM_AUTO_DURATION_INVALID':'plan', 'HQ_LM_AUTO_DURATION_MISSING':'plan',
    'HQ_LM_AUTO_DURATION_MALFORMED':'plan', 'HQ_LM_AUTO_DURATION_OUT_OF_RANGE':'plan',
    'HQ_LM_AUTO_DURATION_CAP_UNAVAILABLE':'plan', 'HQ_LM_METADATA_INVALID':'plan',
    'HQ_WAV_INVALID':'verify', 'HQ_AUDIO_RECEIPT_MISMATCH':'verify',
    'AUDIO_HQ_WAV_FORMAT':'verify', 'AUDIO_HQ_WAV_DURATION':'verify',
    'AUDIO_HQ_AUTO_DURATION_PROOF_MISMATCH':'verify', 'AUDIO_HQ_HUMMING_PROOF_MISMATCH':'verify',
    'AUDIO_HQ_PROVIDER_PROOF':'verify', 'AUDIO_HQ_TEMPO_PROOF_MISMATCH':'verify',
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
