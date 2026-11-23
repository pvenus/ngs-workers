"""CPU-only HDemucs separation. No ACE model, sampling or production writes."""
import base64
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import signal
import stat
import sys
import threading
import wave

RUNTIME=Path.home()/'Workspaces/Workers/runtime/acestep-hq'
SEPARATOR_WEIGHTS='hdemucs_high_trained.pt'
MAX_WAV=24*1024**2
SHA=r'[a-f0-9]{64}'

def unique(pairs):
    out={}
    for key,value in pairs:
        if key in out:raise ValueError
        out[key]=value
    return out

def _separator_checkpoint(root):
    directory=root/'cache/separation'
    pin=directory/'pin.json'
    weights=directory/SEPARATOR_WEIGHTS
    try:
        for path in (directory,pin,weights):
            st=path.lstat()
            if (path.resolve(strict=True)!=path or st.st_uid!=os.getuid() or st.st_mode&0o077
                    or (path==directory and not stat.S_ISDIR(st.st_mode))
                    or (path!=directory and (not stat.S_ISREG(st.st_mode) or st.st_nlink!=1))):
                raise ValueError
        if pin.stat().st_size>4096 or not 0<weights.stat().st_size<=1024**3:
            raise ValueError
        metadata=json.loads(pin.read_bytes(),object_pairs_hook=unique)
        if (set(metadata)!={'schemaVersion','checkpointSha256'} or metadata['schemaVersion']!='ace-hq-hdemucs-pin.v1'
                or type(metadata['checkpointSha256']) is not str or not re.fullmatch(r'[a-f0-9]{64}',metadata['checkpointSha256'])):
            raise ValueError
        return weights,metadata['checkpointSha256']
    except (OSError,ValueError,TypeError):
        raise RuntimeError('HQ_SEPARATOR_UNPROVISIONED') from None


def separate(raw,rate,channels,frames,root,torch,cancelled=lambda:False):
    weights,expected=_separator_checkpoint(root)
    try:
        from torchaudio.functional import resample
        from torchaudio.models import hdemucs_high
    except (ImportError,OSError):
        raise RuntimeError('HQ_SEPARATOR_UNPROVISIONED') from None
    tracks=['drums','bass','other','vocals']
    model=hdemucs_high(sources=tracks)
    fd=os.open(weights,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        st=os.fstat(stream.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.getuid() or st.st_nlink!=1 or st.st_mode&0o077:
            raise RuntimeError('HQ_SEPARATOR_UNPROVISIONED')
        if hashlib.file_digest(stream,'sha256').hexdigest()!=expected:
            raise RuntimeError('HQ_SEPARATOR_UNPROVISIONED')
        stream.seek(0)
        try:
            model.load_state_dict(torch.load(stream,map_location='cpu',weights_only=True),strict=True)
        except (ValueError,RuntimeError,EOFError,TypeError):
            raise RuntimeError('HQ_SEPARATOR_UNPROVISIONED') from None
    model.eval()
    samples=torch.frombuffer(bytearray(raw),dtype=torch.int16).reshape(-1,channels).T.float()/32768.
    if channels==1:samples=samples.repeat(2,1)
    if rate!=44100:samples=resample(samples,rate,44100)
    ref=samples.mean(0); ref_std=ref.std()
    if not torch.isfinite(ref_std) or ref_std<1e-5:
        raise RuntimeError('HQ_SEPARATOR_OUTPUT_EMPTY')
    normalized=(samples-ref.mean())/ref_std
    # Two bounded, staggered contexts share one full-track accumulator.
    mix=_consensus(normalized,model,ref_std,torch,cancelled,segment=10*44100,overlap=44100)

    mix=resample(mix,44100,48000)
    needed=(frames*48000+rate//2)//rate
    if not needed<=mix.shape[-1]<=needed+8:
        raise RuntimeError('HQ_SEPARATOR_DURATION_INVALID')
    mix=mix[:,:needed]
    return (mix.T*32768).round().clamp(-32768,32767).to(torch.int16).contiguous().numpy().tobytes()

def _consensus(samples,model,std,torch,cancelled,*,segment,overlap):
    # Two complementary overlap-add estimates of the same aligned waveform.
    # Averaging retains an identical transient and attenuates a one-grid spike
    # by half, irrespective of song dynamics or where it sits in the segment.
    hop=segment-overlap
    length=samples.shape[-1]
    mix=torch.zeros_like(samples)
    denom=torch.zeros_like(samples[0])
    ramp=(1-torch.cos(torch.linspace(0,math.pi,overlap)))*.5
    grids=(0,hop//2) if length>segment else (0,)
    with torch.inference_mode():
        for offset in grids:
            start=0
            end=min(length,segment-offset)
            while True:
                if cancelled():raise RuntimeError('AUDIO_HQ_CANCELLED')
                actual=end-start
                chunk=samples[:,start:end]
                if actual<2*overlap:
                    chunk=torch.nn.functional.pad(chunk,(0,2*overlap-actual))
                stems=model(chunk.unsqueeze(0))[0]
                if tuple(stems.shape[:2])!=(4,2) or stems.shape[-1]<actual:
                    raise RuntimeError('HQ_SEPARATOR_OUTPUT_EMPTY')
                accompaniment=stems[:3,:,:actual].sum(0)*std
                fade=torch.ones(actual)
                if start:fade[:overlap]=ramp
                if end<length:fade[-overlap:]=1-ramp
                mix[:,start:end]+=accompaniment*fade
                denom[start:end]+=fade
                if end==length:break
                start=end-overlap
                end=min(length,start+segment)
    # Each grid must cover every frame equally, including both endpoint fades.
    if not torch.allclose(denom,torch.full_like(denom,float(len(grids))),rtol=1e-6,atol=1e-6):
        raise RuntimeError('HQ_SEPARATOR_DURATION_INVALID')
    mix.div_(denom)
    if not torch.isfinite(mix).all() or mix.abs().mean()<1e-5:
        raise RuntimeError('HQ_SEPARATOR_OUTPUT_EMPTY')
    return mix

def run(value,root=RUNTIME,cancelled=lambda:False,split=separate):
    try:
        if (type(value) is not dict or set(value)!={'kind','sourceSha256','wavBase64'} or value['kind']!='separate'
                or type(value['sourceSha256']) is not str or not re.fullmatch(SHA,value['sourceSha256'])
                or type(value['wavBase64']) is not str or len(value['wavBase64'])>32*1024**2):
            raise ValueError
        source=base64.b64decode(value['wavBase64'],validate=True)
        if not 44<=len(source)<=MAX_WAV or hashlib.sha256(source).hexdigest()!=value['sourceSha256']:
            raise ValueError
        with wave.open(io.BytesIO(source),'rb') as audio:
            channels,rate,frames=audio.getnchannels(),audio.getframerate(),audio.getnframes()
            raw=audio.readframes(frames)
            if (audio.getsampwidth()!=2 or audio.getcomptype()!='NONE' or channels not in (1,2)
                    or not 8000<=rate<=96000 or not 0<frames/rate<=120 or len(raw)!=frames*channels*2):
                raise ValueError
    except (ValueError,EOFError,wave.Error):
        raise ValueError('HQ_SEPARATOR_SOURCE_INVALID') from None
    if cancelled():raise RuntimeError('AUDIO_HQ_CANCELLED')
    try:
        import torch
    except (ImportError,OSError):
        raise RuntimeError('HQ_SEPARATOR_UNPROVISIONED') from None
    pcm=split(raw,rate,channels,frames,root,torch,cancelled)
    expected=(frames*48000+rate//2)//rate
    if len(pcm)!=expected*4 or len(pcm)+44>MAX_WAV:
        raise RuntimeError('HQ_SEPARATOR_DURATION_INVALID')
    if cancelled():raise RuntimeError('AUDIO_HQ_CANCELLED')
    stream=io.BytesIO()
    with wave.open(stream,'wb') as audio:
        audio.setparams((2,2,48000,0,'NONE','not compressed'))
        audio.writeframes(pcm)
    wav=stream.getvalue()
    if hashlib.sha256(wav).hexdigest()==value['sourceSha256']:
        raise RuntimeError('HQ_SEPARATOR_OUTPUT_UNCHANGED')
    return {'wavBase64':base64.b64encode(wav).decode('ascii'),'bpm':None,'bpmBasis':'unavailable'}

def main():
    stopped=threading.Event()
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,lambda *_:stopped.set())
    cap=32*1024**2+512
    try:
        data=sys.stdin.buffer.read(cap+1)
        if len(data)>cap:raise ValueError('HQ_SEPARATOR_SOURCE_INVALID')
        print(json.dumps(run(json.loads(data,object_pairs_hook=unique),cancelled=stopped.is_set),separators=(',',':')))
    except Exception as exc:
        known={'HQ_SEPARATOR_SOURCE_INVALID':'setup','HQ_SEPARATOR_UNPROVISIONED':'setup',
               'AUDIO_HQ_CANCELLED':'render','HQ_SEPARATOR_DURATION_INVALID':'verify',
               'HQ_SEPARATOR_OUTPUT_UNCHANGED':'verify','HQ_SEPARATOR_OUTPUT_EMPTY':'verify'}
        error=str(exc) if str(exc) in known else 'HQ_SEPARATOR_FAILED'
        print(f'AUDIO_HQ_DIAGNOSTIC stage={known.get(error,"render")} code={error}',file=sys.stderr)
        return 1
    return 0

if __name__=='__main__':raise SystemExit(main())
