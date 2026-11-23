"""CPU-only Mel-Band RoFormer becruily instrumental separation. No ACE model, sampling or production writes."""
import base64
import hashlib
import io
from importlib import metadata
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

RUNTIME=Path.home()/'Workspaces/Workers/runtime/mel-band-roformer-becruily'
ASSETS={
    'model.ckpt': 'a8da6632a1c25efb1c9be783ce9ea367d226d4b918cd6c3717c8b1d7a396041d',
    'model.json': 'e7aee96592352a4a269ef13b071a5d31733cad40c86a458db85aaf4b8c0d056a',
    'arch/models/bs_roformer/mel_band_roformer.py': 'a28fce62020faad6461176660e012cf5799ae8a26c76fe4b2bc636f5b44b9fc5',
    'arch/models/bs_roformer/attend.py': 'c7abbc40a3fd20ff7f6001fa9f8ee9ad5df6452272712a5e719b8f8468bf2223',
}
DEPS={'torch':'2.15.0','numpy':'2.5.5','scipy':'1.18.1','soundfile':'0.14.0',
      'librosa':'0.11.0','beartype':'0.23.1','einops':'0.8.2',
      'rotary-embedding-torch':'0.9.2','packaging':'26.3'}
MODEL_RATE=44100
CHUNK=485100
HOP=CHUNK//2

MAX_WAV=24*1024**2
SHA=r'[a-f0-9]{64}'

def unique(pairs):
    out={}
    for key,value in pairs:
        if key in out:raise ValueError
        out[key]=value
    return out

def _check_runtime(root):
    try:
        if root.resolve(strict=True)!=root:
            raise ValueError
        def check(path,directory):
            st=path.lstat()
            if (st.st_uid!=os.getuid() or st.st_mode&0o077
                    or (directory and not stat.S_ISDIR(st.st_mode))
                    or (not directory and (not stat.S_ISREG(st.st_mode) or st.st_nlink!=1))):
                raise ValueError
        for folder in (root,root/'arch',root/'arch/models',root/'arch/models/bs_roformer'):
            check(folder,True)
        for name,expected in ASSETS.items():
            path=root/name
            check(path,False)
            limit=1_000_000_000 if name=='model.ckpt' else 1_048_576
            with os.fdopen(os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK),'rb') as file:
                st=os.fstat(file.fileno())
                if (not stat.S_ISREG(st.st_mode) or st.st_uid!=os.getuid() or st.st_mode&0o077 or st.st_nlink!=1
                        or not 0<st.st_size<=limit or hashlib.file_digest(file,'sha256').hexdigest()!=expected):
                    raise ValueError
                after=path.lstat()
                if (st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns,st.st_ctime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns):
                    raise ValueError
        if any(metadata.version(name)!=version for name,version in DEPS.items()):
            raise ValueError
    except (OSError,ValueError,metadata.PackageNotFoundError):
        raise RuntimeError('HQ_SEPARATOR_UNPROVISIONED') from None


def _load_model(root,torch):
    config=json.loads((root/'model.json').read_bytes(),object_pairs_hook=unique)
    if (config.get('stereo') is not True or config.get('num_stems')!=1
            or config.get('sample_rate')!=MODEL_RATE):
        raise RuntimeError('HQ_SEPARATOR_UNPROVISIONED')
    config['multi_stft_resolutions_window_sizes']=tuple(config['multi_stft_resolutions_window_sizes'])
    config['flash_attn']=False
    # The private code tree is pinned at both modules, and must contain no
    # additional package initializers or implicit third-party code.
    for directory,entries in [('arch',{'models'}),('arch/models',{'bs_roformer'}),
                              ('arch/models/bs_roformer',{'mel_band_roformer.py','attend.py'})]:
        if {p.name for p in (root/directory).iterdir()}!=entries:
            raise RuntimeError('HQ_SEPARATOR_UNPROVISIONED')
    previous=sys.path[:]
    sys.path.insert(0,str(root/'arch'))
    try:
        from models.bs_roformer.mel_band_roformer import MelBandRoformer
        for name in ('mel_band_roformer','attend'):
            loaded=sys.modules.get(f'models.bs_roformer.{name}')
            if loaded is None or Path(loaded.__file__)!=root/f'arch/models/bs_roformer/{name}.py':
                raise RuntimeError('HQ_SEPARATOR_UNPROVISIONED')
    finally:
        sys.path[:]=previous
    model=MelBandRoformer(**config).cpu().eval()
    state=torch.load(root/'model.ckpt',map_location='cpu',weights_only=True)
    if not isinstance(state,dict) or len(state)!=684 or not all(type(k) is str and isinstance(v,torch.Tensor) for k,v in state.items()):
        raise RuntimeError('HQ_SEPARATOR_UNPROVISIONED')
    model.load_state_dict(state,strict=True)
    return model


def _staggered_overlap(samples,predict,np,cancelled,*,chunk=CHUNK,hop=HOP):
    # Match the approved audition exactly: 11 s chunks, 50% overlap with
    # complementary sine-squared tapers, and zero pad only the final chunk.
    output=np.zeros_like(samples)
    denom=np.zeros(samples.shape[-1],np.float32)
    ramp=np.sin(np.linspace(0,np.pi/2,hop,dtype=np.float32))**2
    for start in range(0,samples.shape[-1],hop):
        if cancelled():raise RuntimeError('AUDIO_HQ_CANCELLED')
        end=min(start+chunk,samples.shape[-1])
        length=end-start
        part=samples[:,start:end]
        instrumental=predict(part,chunk)
        if instrumental.shape!=(2,chunk) or not np.isfinite(instrumental).all():
            raise RuntimeError('HQ_SEPARATOR_OUTPUT_EMPTY')
        window=np.ones(chunk,np.float32)
        if start:window[:hop]=ramp
        if end<samples.shape[-1]:window[-hop:]=1.0-ramp
        output[:,start:end]+=instrumental[:,:length]*window[:length]
        denom[start:end]+=window[:length]
        if end==samples.shape[-1]:break
    if not np.all(denom>0):
        raise RuntimeError('HQ_SEPARATOR_DURATION_INVALID')
    return output/denom


def separate(raw,rate,channels,frames,root,cancelled=lambda:False):
    _check_runtime(root)
    if cancelled():raise RuntimeError('AUDIO_HQ_CANCELLED')
    try:
        import numpy as np
        import soundfile as sf
        from scipy.signal import resample_poly
        import torch
        torch.set_num_threads(3)
        torch.set_num_interop_threads(1)
        model=_load_model(root,torch)
    except (ImportError,OSError,ValueError,TypeError,AttributeError,RuntimeError):
        raise RuntimeError('HQ_SEPARATOR_UNPROVISIONED') from None
    if cancelled():raise RuntimeError('AUDIO_HQ_CANCELLED')
    source=np.frombuffer(raw,dtype='<i2').reshape(frames,channels).astype(np.float32)/32768.
    if channels==1:source=np.repeat(source,2,axis=1)
    divisor=math.gcd(MODEL_RATE,rate)
    samples=resample_poly(source,MODEL_RATE//divisor,rate//divisor,axis=0).astype(np.float32).T.copy()
    def predict(part,chunk):
        tensor=torch.from_numpy(part.copy())
        if part.shape[-1]<chunk:tensor=torch.nn.functional.pad(tensor,(0,chunk-part.shape[-1]))
        with torch.inference_mode():
            result=model(tensor.unsqueeze(0)).detach().cpu().numpy()
        if result.ndim==4 and result.shape[1]==1:result=result[:,0]
        if result.shape!=(1,2,chunk):
            raise RuntimeError('HQ_SEPARATOR_OUTPUT_EMPTY')
        return result[0]
    inst=_staggered_overlap(samples,predict,np,cancelled)
    output=resample_poly(inst.T,160,147,axis=0)
    needed=(frames*48000+rate//2)//rate
    if not needed<=output.shape[0]<=needed+3:
        raise RuntimeError('HQ_SEPARATOR_DURATION_INVALID')
    output=output[:needed]
    peak=float(np.max(np.abs(output)))
    if not .001<peak<8:
        raise RuntimeError('HQ_SEPARATOR_OUTPUT_EMPTY')
    with io.BytesIO() as stream:
        sf.write(stream,output*min(1.,.999/peak),48000,format='RAW',subtype='PCM_16',endian='LITTLE')
        return stream.getvalue()

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
    pcm=split(raw,rate,channels,frames,root,cancelled)
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
