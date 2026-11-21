"""Model-only alpha processing of exact decoded frames; no video re-encoding."""
from __future__ import annotations
import hashlib,json,os,sys,time,resource
from pathlib import Path
import numpy as np
from PIL import Image
from workers.resident.postprocess.alpha_models import BiRefNetMatting
from workers.resident.postprocess.alpha_core import fuse_mattes,stabilize_alpha,warp_previous_alpha


def main():
    root=Path(sys.argv[1]); key=sys.argv[2]; started=time.monotonic()
    extraction=json.loads((root/'extraction.json').read_text()); raw=sorted((root/'raw').glob('frame-*.png'))
    import torch
    device='mps' if torch.backends.mps.is_available() else ('cuda' if torch.cuda.is_available() else 'cpu')
    matting=BiRefNetMatting(os.environ['FRAME_ALPHA_MATTING_MODEL'],device=device)
    from sam2.build_sam import build_sam2_video_predictor
    tracker=build_sam2_video_predictor('configs/sam2.1/sam2.1_hiera_s.yaml',os.environ['FRAME_ALPHA_TRACKING_MODEL'],device=device,apply_postprocessing=False)
    # SAM2 requires numeric filenames. Hardlinks are lossless and avoid duplicate raw storage.
    trackdir=root/'tracking-input'; trackdir.mkdir(exist_ok=True)
    for i,path in enumerate(raw):
        target=trackdir/f'{i:06d}.png'
        if not target.exists(): os.link(path,target)
    guard=root/'alpha-work.json'
    identity={'modelKey':key,'sourceSha256':extraction['sourceSha256']}
    if guard.exists() and json.loads(guard.read_text())!=identity: raise RuntimeError('alpha_partial_identity_changed')
    guard.write_text(json.dumps(identity))
    first=np.asarray(Image.open(raw[0]).convert('RGB'))
    coarse=root/'tracking-masks.npz'; calls={'birefnet':0,'sam2VideoTracking':0}
    if coarse.exists():
        with np.load(coarse,allow_pickle=False) as saved: masks={int(k):saved[k] for k in saved.files}
    else:
        initial=matting.predict(first); calls['birefnet']+=1
        if not (initial>=0.5).any(): raise RuntimeError('alpha_no_subject_initial_mask')
        # Keep all learned foreground components, including separated sword pixels.
        jpeg=root/'tracking-jpeg'; jpeg.mkdir(exist_ok=True)
        for i,path in enumerate(raw):
            with Image.open(path) as im: im.convert('RGB').save(jpeg/f'{i:06d}.jpg',quality=95)
        with torch.inference_mode():
            state=tracker.init_state(video_path=str(jpeg),offload_video_to_cpu=True,offload_state_to_cpu=True)
            tracker.add_new_mask(state,frame_idx=0,obj_id=1,mask=initial>=0.5)
            masks={int(i):(logits[0,0].cpu().numpy()>0).astype(np.float32) for i,_,logits in tracker.propagate_in_video(state)}
        calls['sam2VideoTracking']+=1
        if sorted(masks)!=list(range(len(raw))): raise RuntimeError('alpha_tracker_frame_indices_mismatch')
        with (root/'tracking-masks.npz.tmp').open('wb') as stream: np.savez_compressed(stream,**{str(i):v for i,v in masks.items()})
        (root/'tracking-masks.npz.tmp').replace(coarse)
    if sorted(masks)!=list(range(len(raw))): raise RuntimeError('alpha_tracker_frame_indices_mismatch')
    out=root/'alpha'; out.mkdir(exist_ok=True)
    previous=previous_rgb=None; hashes=[]; diagnostics=[]
    for i,path in enumerate(raw):
        rgb=np.asarray(Image.open(path).convert('RGB'))
        dest=out/f'frame-{i:08d}.png'; receipt=out/f'frame-{i:08d}.json'
        if dest.exists() and receipt.exists():
            h=hashlib.sha256(dest.read_bytes()).hexdigest()
            if json.loads(receipt.read_text()).get('sha256')!=h: raise RuntimeError('alpha_partial_frame_corrupted')
            previous=np.asarray(Image.open(dest).getchannel('A'),dtype=np.float32)/255
            previous_rgb=rgb;hashes.append(h);continue
        alpha=fuse_mattes(masks[i],matting.predict(rgb),radius=6); calls['birefnet']+=1
        if previous is not None:
            warped=warp_previous_alpha(previous,previous_rgb,rgb)
            diagnostics.append({'frame':i,'alphaDelta':float(np.mean(np.abs(alpha-warped)))})
            alpha=stabilize_alpha(alpha,warped,0.0)
        rgba=np.dstack((rgb,np.round(np.clip(alpha,0,1)*255).astype('uint8')))
        dest=out/f'frame-{i:08d}.png'; Image.fromarray(rgba,'RGBA').save(dest)
        h=hashlib.sha256(dest.read_bytes()).hexdigest(); hashes.append(h)
        receipt.write_text(json.dumps({'sha256':h})); previous=alpha; previous_rgb=rgb
    summary={'modelKey':key,'sourceSha256':extraction['sourceSha256'],'frameCount':len(raw),'alphaSha256':hashes,'modelSeconds':round(time.monotonic()-started,3),'generativeModelCalls':0,'segmentationCalls':{'birefnet':len(raw)+1,'sam2VideoTracking':1},'thisAttemptSegmentationCalls':calls,'peakRssBytes':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,'device':device,'mattingModel':os.environ['FRAME_ALPHA_MATTING_MODEL'],'trackingModel':os.environ['FRAME_ALPHA_TRACKING_MODEL'],'sam2CudaPostprocessing':False,'temporalStrength':0.0,'diagnostics':diagnostics}
    temp=root/'alpha-summary.json.tmp'; temp.write_text(json.dumps(summary,indent=2)); temp.replace(root/'alpha-summary.json')

if __name__=='__main__': main()
