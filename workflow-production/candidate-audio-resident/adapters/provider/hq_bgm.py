"""One existing HQ plan and render, owned private scratch, no fallback."""
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import tempfile
import wave

from music_boundary import HQ_PROFILE,_music_boundary_from_binding

LM_REV='0a3ec94b557aea7d508da38b31cfe7341f6ff737'
XL_REV='d06de46b4622f781cf07f4a013a67d591ca52819'
RUNTIME_REV='ca1e85fe9430179831e6bc6be790c332190a3866'
AUTO_MAX_SECONDS=120
MAX_WAV_BYTES=24*1024**2

def _file(path,limit):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        before=os.fstat(stream.fileno())
        if (not stat.S_ISREG(before.st_mode) or before.st_uid!=os.getuid() or before.st_nlink!=1
                or before.st_mode&0o022 or not 0<before.st_size<=limit):
            raise ValueError('AUDIO_HQ_FILE_INVALID')
        data=stream.read(limit+1)
        def stamp(s):return(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
        if len(data)!=before.st_size or stamp(before)!=stamp(os.fstat(stream.fileno())) or stamp(before)!=stamp(path.lstat()):
            raise ValueError('AUDIO_HQ_FILE_CHANGED')
    return data

def _validated_wav(path,duration):
    # None means actual model duration is verified against the returned plan below.
    import io
    data=_file(path,MAX_WAV_BYTES if duration is None else 6*1024**2)
    with wave.open(io.BytesIO(data),'rb') as wav:
        if (wav.getframerate(),wav.getnchannels(),wav.getsampwidth(),wav.getcomptype()) != (48000,2,2,'NONE'):
            raise ValueError('AUDIO_HQ_WAV_FORMAT')
        frames=wav.getnframes()
        actual=frames/48000
        if ((duration is not None and abs(actual-duration)>.1) or
                (duration is None and not 9.9<=actual<=AUTO_MAX_SECONDS+.1) or
                len(wav.readframes(frames))!=frames*4):
            raise ValueError('AUDIO_HQ_WAV_DURATION')
    return data,actual

def _proof(working,proof,duration,seed,wav,actual,text,target=None):
    wav_sha=hashlib.sha256(wav).hexdigest()
    if not isinstance(proof,dict):raise ValueError('AUDIO_HQ_PROVIDER_PROOF')
    job_sha=proof.get('request_sha256')
    if (type(job_sha)is not str or not re.fullmatch(r'[a-f0-9]{64}',job_sha)
        or proof.get('model')!=HQ_PROFILE or proof.get('synthetic_test_artifact')is not False
        or proof.get('audio_sha256')!=wav_sha or proof.get('planner_snapshot')!=LM_REV
        or proof.get('weights_snapshot')!=XL_REV or proof.get('runtime_commit')!=RUNTIME_REV):
        raise ValueError('AUDIO_HQ_PROVIDER_PROOF')
    root=working/'audio.hq'
    if target is not None:
        attempt=proof.get('plan_attempt')
        if type(attempt) is not int or not 1<=attempt<=5:
            raise ValueError('AUDIO_HQ_AUTO_DURATION_PROOF_MISMATCH')
        seed=(seed+(attempt-1)*0x9e3779b9)&0x7fffffff
        if type(proof.get('seed')) is not int or proof['seed']!=seed:
            raise ValueError('AUDIO_HQ_AUTO_DURATION_PROOF_MISMATCH')
        root=root/f'attempt-{attempt}'
    if root.resolve(strict=True)!=root or not root.is_dir():raise ValueError('AUDIO_HQ_PROOF_SCOPE')
    raw_plan=_file(root/'lm-result.json',1024**2)
    observe=json.loads(raw_plan)
    result=json.loads(_file(root/'render-result.json',1024**2))
    settings=json.loads(_file(root/'generation-settings.json',1024**2))
    plan_sha=hashlib.sha256(raw_plan).hexdigest()
    if (observe.get('schemaVersion')!='ace-hq-plan.v1' or observe.get('success') is not True
        or observe.get('requestSha256')!=job_sha or observe.get('model')!='acestep-5Hz-lm-4B'
        or observe.get('modelRevision')!=LM_REV or observe.get('seed')!=seed
        or result.get('schemaVersion')!='ace-hq-render.v1' or result.get('success') is not True
        or result.get('requestSha256')!=job_sha or result.get('audioSha256')!=wav_sha
        or result.get('audioBytes')!=len(wav) or result.get('lmResultSha256')!=plan_sha
        or settings.get('requestSha256')!=job_sha or settings.get('lmResultSha256')!=plan_sha):
        raise ValueError('AUDIO_HQ_TEMPO_PROOF_MISMATCH')
    metadata=observe.get('metadata')
    params=settings.get('params')
    if type(metadata)is not dict or type(params)is not dict:raise ValueError('AUDIO_HQ_TEMPO_PROOF_MISMATCH')
    planned=observe.get('duration')
    if duration is None:
        if (type(planned) not in (int,float) or not math.isfinite(planned) or not 10<=planned<=AUTO_MAX_SECONDS
            or metadata.get('duration')!=planned or params.get('duration')!=planned
            or abs(actual-planned)>.1 or result.get('durationSeconds')!=actual
            or proof.get('duration_seconds')!=actual):
            raise ValueError('AUDIO_HQ_AUTO_DURATION_PROOF_MISMATCH')
        if text and (params.get('instrumental') is not False or params.get('lyrics')!=text):
            raise ValueError('AUDIO_HQ_HUMMING_PROOF_MISMATCH')
    elif planned!=duration:
        raise ValueError('AUDIO_HQ_TEMPO_PROOF_MISMATCH')
    if target is not None and (planned<target or actual<target):
        raise ValueError('AUDIO_HQ_FULL_DURATION_NOT_MET')
    model_bpm=metadata.get('bpm');render_bpm=params.get('bpm')
    if model_bpm is None and render_bpm is None:return {'bpm':None,'bpmBasis':'unavailable'}
    if (type(model_bpm) not in (int,float) or not math.isfinite(model_bpm) or int(model_bpm)!=model_bpm
        or not 30<=model_bpm<=300 or render_bpm!=model_bpm):
        raise ValueError('AUDIO_HQ_TEMPO_PROOF_MISMATCH')
    return {'bpm':int(model_bpm),'bpmBasis':'model'}

def _scratch_parent(config):
    if config.get('schema')!='private.music-hq-boundary.v1':
        raise ValueError('AUDIO_HQ_BINDING_INVALID')
    parent=Path(config['artifact_root'])
    if not parent.is_absolute() or parent.resolve(strict=True)!=parent or not parent.is_dir() or parent.stat().st_uid!=os.getuid() or parent.stat().st_mode&0o077:
        raise ValueError('AUDIO_HQ_SCRATCH_INVALID')
    return parent


def render_cover_hq(value,binding,cancelled=lambda:False,*,connect=_music_boundary_from_binding):
    if (type(value) is not dict or set(value)!={'kind','sourceSha256','wavBase64'} or value['kind']!='separate'
            or type(value['sourceSha256']) is not str or not re.fullmatch(r'[a-f0-9]{64}',value['sourceSha256'])
            or type(value['wavBase64']) is not str or len(value['wavBase64'])>32*1024**2):
        raise ValueError('AUDIO_HQ_INPUT_INVALID')
    try:
        import io
        source=base64.b64decode(value['wavBase64'],validate=True)
        sha=value['sourceSha256']
        if not 44<=len(source)<=MAX_WAV_BYTES or hashlib.sha256(source).hexdigest()!=sha:
            raise ValueError('hash')
        with wave.open(io.BytesIO(source),'rb') as wav:
            frames,channels,sample_rate=wav.getnframes(),wav.getnchannels(),wav.getframerate()
            if (wav.getsampwidth()!=2 or wav.getcomptype()!='NONE' or channels not in (1,2) or not 8000<=sample_rate<=96000
                    or not 10<=frames/sample_rate<=AUTO_MAX_SECONDS or len(wav.readframes(frames))!=2*channels*frames):
                raise ValueError('wav')
        duration=frames/sample_rate
    except (ValueError,EOFError,wave.Error):
        raise ValueError('HQ_COVER_SOURCE_INVALID') from None
    if cancelled():raise RuntimeError('AUDIO_HQ_CANCELLED')
    provider,config=connect(HQ_PROFILE,binding)
    parent=_scratch_parent(config)
    # Use a source-bound deterministic seed. There is no request-controlled prompt.
    seed=int.from_bytes(hashlib.sha256(b'workflow.audio-separate.v1\0'+sha.encode()).digest()[:4],'big')&0x7fffffff
    with tempfile.TemporaryDirectory(prefix='content.workflow-bgm-',dir=parent) as name:
        root=Path(name)
        fd=os.open(root/'source.wav',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'wb') as stream:
            stream.write(source);stream.flush();os.fsync(stream.fileno())
        wav=root/'audio.wav'
        proof=provider.generate_music(config,root,None,duration,seed,HQ_PROFILE,wav,cancelled,
            timeout=1100,cover_sha256=sha)
        if cancelled():raise RuntimeError('AUDIO_HQ_CANCELLED')
        data,actual=_validated_wav(wav,None)
        digest=hashlib.sha256(data).hexdigest()
        if (not isinstance(proof,dict) or proof.get('synthetic_test_artifact') is not False
                or proof.get('model')!=HQ_PROFILE or proof.get('cover_source_sha256')!=sha
                or not re.fullmatch(r'[a-f0-9]{64}',proof.get('accompaniment_sha256') or '')
                or proof['accompaniment_sha256']==sha or proof.get('audio_sha256')!=digest or digest==sha
                or proof.get('duration_seconds')!=actual or abs(actual-duration)>.100000001
                or proof.get('weights_snapshot')!=XL_REV or proof.get('runtime_commit')!=RUNTIME_REV):
            raise ValueError('AUDIO_HQ_COVER_PROOF_INVALID')
        return {'wavBase64':base64.b64encode(data).decode('ascii'),'bpm':None,'bpmBasis':'unavailable'}


def render_hq(value,binding,cancelled=lambda:False,*,connect=_music_boundary_from_binding):
    fields={'kind','title','prompt','durationSeconds'}
    if (type(value)is not dict or set(value)-{'text','targetDurationSeconds'}!=fields or value['kind']!='bgm'
        or type(value['title'])is not str or not value['title'].strip() or len(value['title'])>120
        or type(value['prompt'])is not str or not value['prompt'].strip() or len(value['prompt'])>2000 or '\0' in value['prompt']
        or (value['durationSeconds'] is not None and (type(value['durationSeconds']) not in (int,float)
            or not math.isfinite(value['durationSeconds']) or not 10<=value['durationSeconds']<=30))):
        raise ValueError('AUDIO_HQ_INPUT_INVALID')
    target=value.get('targetDurationSeconds')
    if 'targetDurationSeconds' in value and (value['durationSeconds'] is not None or type(target) is not int or target!=60):
        raise ValueError('AUDIO_HQ_INPUT_INVALID')
    text=value.get('text','')
    if type(text)is not str or len(text)>4000 or '\0' in text or (text and (not text.strip() or value['durationSeconds'] is not None)):
        raise ValueError('AUDIO_HQ_INPUT_INVALID')
    if cancelled():raise RuntimeError('AUDIO_HQ_CANCELLED')
    provider,config=connect(HQ_PROFILE,binding)
    parent=_scratch_parent(config)
    duration=value['durationSeconds']
    raw=json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
    seed=int.from_bytes(hashlib.sha256(b'workflow.bgm.hq.v1\0'+raw).digest()[:4],'big')&0x7fffffff
    with tempfile.TemporaryDirectory(prefix='content.workflow-bgm-',dir=parent) as name:
        root=Path(name)
        wav=root/'audio.wav'
        # No requested BPM: if the LM provides it, it is model metadata only.
        settings={'instrumental':False,'lyrics':text,'vocal_language':'ko'} if text else {}
        if target is not None:settings['target_duration_seconds']=target
        proof=provider.generate_music(config,root,value['prompt'],duration,seed,HQ_PROFILE,wav,cancelled,timeout=1100,settings=settings)
        if cancelled():raise RuntimeError('AUDIO_HQ_CANCELLED')
        data,actual=_validated_wav(wav,duration)
        return {'wavBase64':base64.b64encode(data).decode('ascii'),**_proof(root,proof,duration,seed,data,actual,text,target)}
