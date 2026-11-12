"""Local review catalog and sample-domain measurements. No model loading."""
from __future__ import annotations
import array, hashlib, json, math, re, sys, wave
from pathlib import Path

ID = re.compile(r'^[0-9a-f]{32}$')
TARGETS = ['9e55522d628b4cc2834516fa027d0624','691d5f8bd3c3498e80fa01c2ecad7ead','bcf6ed874d2946f1a2986d960eca21e9','10556f5af4df4d2eb870b06270235fb9']
TITLES = dict(zip(TARGETS,['A · 여정의 여운','B · 마을의 잔치','C · 결의의 전진','이전 · 한국풍 축제']))

HISTORY = {
 '0e92c3bd62384e74bcf5de196cf860d7': ('풍악 전투 · 중간 시도 3102','전투','이전 제외 후보(클리핑)'),
 '66396407dae84463b44515c9e4716e80': ('풍악 전투 · 중간 시도 3103','전투','이전 제외 후보(클리핑)'),
 '4971ad66e9694ffe91a90cb080b6dd12': ('풍악 전투 · 중간 시도 3104','전투','이전 제외 후보(클리핑)'),
 '79dbfda964f649ebae80e9e5a3f97515': ('아리랑','한국풍 음악 후보',None),
 'a8eb3ced575b4acd980a7d6a26816c80': ('풍악 축제','축제',None),
 '68d2fbe3a4424386af3d1d18a0ce6ebc': ('풍악 전투','전투',None),
 '8ea3af18ef9b4590b681ab5091317c66': ('초기 마을','마을 휴식',None),
 '8792e9a354ac4fb79813d478243c361e': ('초기 던전','던전 탐험',None),
 'de9e9c6c0bb64972ae6b10e67275c69b': ('초기 전투','전투',None),
}

def safe_file(root: Path, artifact_id: str, filename: str) -> Path:
    if not ID.fullmatch(artifact_id): raise ValueError('invalid artifact ID')
    root = root.resolve(); path = root / artifact_id / filename
    resolved = path.resolve()
    if not resolved.is_relative_to(root) or path.is_symlink() or path.parent.is_symlink():
        raise ValueError('unsafe artifact path')
    return resolved

def load_json(path): return json.loads(Path(path).read_text(encoding='utf-8'))

def atomic_json(path, value):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix('.tmp'); tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8'); tmp.replace(path)

def measure_audio(path: Path):
    with wave.open(str(path),'rb') as wav:
        if wav.getsampwidth()!=2 or wav.getcomptype()!='NONE': raise ValueError('DSP supports PCM16 WAV only')
        rate,channels,frames=wav.getframerate(),wav.getnchannels(),wav.getnframes()
        if not frames: raise ValueError('empty WAV')
        peak=clipped=total=0; energy=0; quiet_frames=0; windows=[]; waveform=[]; offset=0
        while raw:=wav.readframes(max(1,rate//10)):
            data=array.array('h',raw)
            if sys.byteorder!='little': data.byteswap()
            count=len(data); local_peak=max(abs(x) for x in data); squares=sum(x*x for x in data)
            peak=max(peak,local_peak); clipped+=sum(x in (-32768,32767) for x in data); total+=count; energy+=squares
            rms=math.sqrt(squares/count)/32768; nframes=count//channels
            if rms<.001: quiet_frames+=nframes; windows.append(round(offset/rate,3))
            waveform.append(round(local_peak/32768,5)); offset+=nframes
    db=lambda x: round(20*math.log10(x),4) if x else None
    return {'status':'measured','method':'PCM16 sample-domain v1','duration_seconds':frames/rate,'sample_rate':rate,'channels':channels,'sample_peak_dbfs':db(peak/32768),'rms_dbfs':db(math.sqrt(energy/total)/32768),'full_scale_sample_count':clipped,'full_scale_sample_ratio':clipped/total,'silence_window_seconds':.1,'silence_threshold_dbfs':-60,'silence_seconds':quiet_frames/rate,'silence_window_starts':windows,'waveform_peak_100ms':waveform,'integrated_lufs':None,'lufs_status':'미측정: LUFS 도구 없음','limitations':'샘플 피크는 true peak가 아니며 full-scale 샘플은 클리핑 가능 지표입니다. 지각적 왜곡·음악성·박자·조성을 측정하지 않습니다. 무음은 100ms RMS < -60dBFS 기준입니다.'}

def catalog(artifact_dir: Path, review_dir: Path):
    tracks=[]
    for folder in sorted(artifact_dir.iterdir()):
        if not ID.fullmatch(folder.name): continue
        try:
            metadata=load_json(safe_file(artifact_dir,folder.name,'metadata.json'))
            if metadata.get('kind')!='music' or metadata.get('synthetic_test_artifact') is not False or metadata.get('legacy') is True: continue
            audio=safe_file(artifact_dir,folder.name,'audio.wav')
            history=HISTORY.get(folder.name)
            record={'artifact_id':folder.name,'review_order':metadata.get('review_order',100),'title':metadata.get('title') or TITLES.get(folder.name,history[0] if history else 'BGM · '+folder.name[:8]),'original_use':history[1] if history else None,'historical_exclusion':history[2] if history else None,'history_source':'총괄의 기존 생성 기록 인계 2026-09-15' if history else None,'audio_url':f'/v1/reviews/{folder.name}/audio','audio_available':audio.is_file(),'generation':metadata,'status':'unreviewed','total_score':None,'score':None,'dsp':None}
            result=safe_file(review_dir,folder.name,'result.json')
            if result.is_file():
                review=load_json(result); record['review']=review; record['status']=review.get('status','unreviewed'); record['total_score']=review.get('total_score'); record['score']=review.get('score'); record['dsp']=review.get('dsp')
            tracks.append(record)
        except (OSError,ValueError) as e:
            # Do not silently turn an existing real track's invalid review into success.
            if 'record' in locals() and record.get('artifact_id')==folder.name:
                record.update(status='error',total_score=None,score=None,error=type(e).__name__+': '+str(e)); tracks.append(record)
    tracks.sort(key=lambda x:(x.get('review_order',100),bool(x.get('historical_exclusion')),TARGETS.index(x['artifact_id']) if x['artifact_id'] in TARGETS else len(TARGETS),x['artifact_id']))
    return {'schema_version':1,'notice':'기술 측정은 실제 WAV에서 계산했습니다. 음악적 표현과 기획 일치도는 직접 듣고 비교하세요.','tracks':tracks}

def validate_score(raw: str, criteria: list, duration: float | None = None):
    text=raw.strip()
    if text.startswith("```json") and text.endswith("```"): text=text[7:-3].strip()
    elif text.startswith("```") and text.endswith("```"): text=text[3:-3].strip()
    value=json.loads(text)
    if not isinstance(value,dict) or not isinstance(value.get('scores'),list): raise ValueError('missing scores')
    expected={c['id']:c for c in criteria}; seen=set(); cleaned=[]
    for item in value['scores']:
        key=item.get('criterion_id'); score=item.get('score')
        if key not in expected or key in seen: raise ValueError('unknown or duplicate criterion')
        if score is not None and (isinstance(score,bool) or not isinstance(score,(int,float)) or not math.isfinite(score) or not 0<=score<=expected[key]['max_points'] or score*2!=int(score*2)): raise ValueError('invalid score')
        evidence=item.get('evidence')
        if not isinstance(evidence,list) or (score is not None and not evidence): raise ValueError('missing evidence')
        for e in evidence:
            if not isinstance(e,dict) or not all(isinstance(e.get(k),str) and e[k].strip() for k in ('segment','observation','interpretation')): raise ValueError('invalid evidence')
        for e in evidence:
            start,end=e.get('start_seconds'),e.get('end_seconds')
            for stamp in (start,end):
                if stamp is not None and (isinstance(stamp,bool) or not isinstance(stamp,(int,float)) or not math.isfinite(stamp) or stamp<0 or (duration is not None and stamp>duration)): raise ValueError('invalid timestamp')
            if start is not None and end is not None and start>end: raise ValueError('reversed timestamps')
        if not isinstance(item.get('reason'),str) or not item['reason'].strip(): raise ValueError('missing reason')
        if item.get('confidence') not in ('low','medium','high',None): raise ValueError('invalid confidence')
        if not isinstance(item.get('uncertainty'),list) or not item['uncertainty'] or not all(isinstance(x,str) and x.strip() for x in item['uncertainty']): raise ValueError('missing uncertainty')
        seen.add(key); cleaned.append({**item,'label':expected[key]['name'],'max_points':expected[key]['max_points']})
    if seen!=set(expected) or sum(c['max_points'] for c in criteria)!=100: raise ValueError('incomplete rubric')
    total=round(sum(i['score'] for i in cleaned),2) if all(i['score'] is not None for i in cleaned) else None
    return {'scores':cleaned,'total_score':total,'total_calculated_by':'code','scale':100,'subjective':True}
