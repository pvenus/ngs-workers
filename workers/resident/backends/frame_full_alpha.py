"""Lossless all-frame extraction and isolated model matting. Never chroma fallback."""
from __future__ import annotations
import hashlib,json,os,shutil,subprocess,sys,time
from fractions import Fraction
from pathlib import Path
from PIL import Image


def _hash(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''): h.update(block)
    return h.hexdigest()


def extract_video(source:Path,folder:Path):
    source=Path(source).resolve(); folder=Path(folder).resolve()
    probe=json.loads(subprocess.check_output([os.environ['FRAME_FFPROBE'],'-v','error','-select_streams','v:0','-show_frames','-show_streams','-show_entries','stream=width,height,avg_frame_rate,time_base:frame=best_effort_timestamp,best_effort_timestamp_time,duration_time,pkt_duration_time','-of','json',str(source)],text=True))
    frames=probe.get('frames',[]); stream=probe['streams'][0]
    if not frames: raise RuntimeError('alpha_source_has_no_frames')
    fingerprint=_hash(source); raw=folder/'raw'; receipt=folder/'extraction.json'
    if receipt.exists():
        saved=json.loads(receipt.read_text())
        if saved['sourceSha256']!=fingerprint: raise RuntimeError('alpha_source_changed')
        paths=[raw/f'frame-{i:08d}.png' for i in range(len(frames))]
        if len(saved['rawSha256'])!=len(paths) or len(list(raw.glob('*.png')))!=len(paths) or any(not p.is_file() or _hash(p)!=h for p,h in zip(paths,saved['rawSha256'])): raise RuntimeError('alpha_raw_receipt_mismatch')
        return saved,paths
    folder.mkdir(parents=True,exist_ok=True)
    raw.mkdir(exist_ok=True)
    if list(raw.iterdir()): raise RuntimeError('alpha_partial_extraction_requires_recovery')
    subprocess.run([os.environ['FRAME_FFMPEG'],'-nostdin','-v','error','-i',str(source),'-map','0:v:0','-fps_mode','passthrough','-start_number','0',str(raw/'frame-%08d.png')],check=True)
    paths=sorted(raw.glob('frame-*.png'))
    if len(paths)!=len(frames): raise RuntimeError('alpha_extracted_frame_count_mismatch')
    sizes=[]
    for p in paths:
        with Image.open(p) as im: sizes.append(im.size)
    if any(size!=(stream['width'],stream['height']) for size in sizes): raise RuntimeError('alpha_raw_dimensions_changed')
    value={'sourceSha256':fingerprint,'frameCount':len(paths),'fps':float(Fraction(stream['avg_frame_rate'])),'width':stream['width'],'height':stream['height'],'timeBase':stream.get('time_base'),'frames':[{'index':i,'pts':f.get('best_effort_timestamp'),'ptsSeconds':f.get('best_effort_timestamp_time'),'durationSeconds':f.get('duration_time',f.get('pkt_duration_time'))} for i,f in enumerate(frames)],'rawSha256':[_hash(p) for p in paths]}
    receipt.write_text(json.dumps(value,indent=2))
    return value,paths


def process_video(source:Path,folder:Path):
    folder=Path(folder).resolve(); start=time.monotonic()
    extraction,raw=extract_video(source,folder)
    config={k:os.environ.get(k,'') for k in ('FRAME_ALPHA_PYTHON','FRAME_ALPHA_MODULE_ROOT','FRAME_ALPHA_MATTING_MODEL','FRAME_ALPHA_TRACKING_MODEL')}
    if any(not v for v in config.values()): raise RuntimeError('alpha_model_not_configured: '+','.join(k for k,v in config.items() if not v))
    model_key=hashlib.sha256(('frame-alpha-v2-temporal0:'+json.dumps(config,sort_keys=True)).encode()).hexdigest()
    receipt=folder/'alpha-summary.json'
    if not receipt.exists():
        env=dict(os.environ); env['HF_HUB_OFFLINE']='1'; env['TRANSFORMERS_OFFLINE']='1'; env['PYTORCH_ENABLE_MPS_FALLBACK']='1'
        env['PYTHONPATH']=config['FRAME_ALPHA_MODULE_ROOT']+os.pathsep+env.get('PYTHONPATH','')
        with (folder/'alpha-model.log').open('a') as log:
            subprocess.run([config['FRAME_ALPHA_PYTHON'],str(Path(__file__).with_name('frame_full_alpha_cli.py')),str(folder),model_key],env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
    summary=json.loads(receipt.read_text())
    if summary['modelKey']!=model_key or summary['sourceSha256']!=extraction['sourceSha256']: raise RuntimeError('alpha_completed_source_or_model_changed')
    alpha=[folder/'alpha'/f'frame-{i:08d}.png' for i in range(len(raw))]
    if len(list((folder/'alpha').glob('*.png')))!=len(raw): raise RuntimeError('alpha_frame_count_mismatch')
    for i,p in enumerate(alpha):
        with Image.open(p) as im:
            if im.mode!='RGBA' or im.size!=(extraction['width'],extraction['height']): raise RuntimeError('alpha_rgba_contract_invalid')
        if _hash(p)!=summary['alphaSha256'][i]: raise RuntimeError('alpha_frame_hash_mismatch')
    return {'rawFrames':[str(p) for p in raw],'alphaFrames':[str(p) for p in alpha],'fps':extraction['fps'],'frameCount':len(raw),'frameTimes':[float(f['ptsSeconds']) for f in extraction['frames']],'trace':{**extraction,**summary,'adapterSeconds':round(time.monotonic()-start,3)}}

if __name__=='__main__':
    result=process_video(Path(sys.argv[1]),Path(sys.argv[2]))
    (Path(sys.argv[2])/'pipeline-result.json').write_text(json.dumps(result))
