"""Real CPU frame postprocessing, using the previously deployed common palette code.

Input is an already-materialized PDS alpha-frame set, not a direct Filex path.
Video extraction/segmentation and final PDS publication are separate adapters.
"""
import hashlib
import json
import os
from pathlib import Path

DIRECTIONS=('n','ne','e','se','s','sw','w','nw')


class FramePostprocessBackend:
    def load(self):pass
    def unload(self):pass

    def execute(self,claim,workspace,cancel):
        if claim.capability!='frames.extract':raise ValueError('FRAME_CAPABILITY_REQUIRED')
        root=workspace.resolve(strict=True)
        manifest=root/'frame-inputs.json'
        if manifest.is_symlink():raise ValueError('FRAME_MANIFEST_SYMLINK')
        staged=json.loads(manifest.read_text())
        if (staged.get('requestId')!=claim.request_id or staged.get('attemptId')!=claim.attempt_id
                or staged.get('expectedRevision')!=claim.expected_revision):
            raise ValueError('FRAME_INPUT_BINDING_MISMATCH')
        inputs=staged['directions']
        if set(inputs)!=set(DIRECTIONS):raise ValueError('FRAME_DIRECTIONS_REQUIRED')
        for item in inputs.values():
            if len(item['rawFrames'])!=len(item['alphaFrames']) or len(item['rawFrames'])!=item['frameCount']:
                raise ValueError('FRAME_INPUT_COUNT_MISMATCH')
            for name in (*item['rawFrames'],*item['alphaFrames']):
                path=Path(name)
                if path.is_symlink() or root not in path.resolve(strict=True).parents:
                    raise ValueError('FRAME_INPUT_OUTSIDE_WORKSPACE')
                if hashlib.sha256(path.read_bytes()).hexdigest()+'.png'!=path.name:
                    raise ValueError('FRAME_INPUT_HASH_MISMATCH')
        if cancel.is_set():raise RuntimeError('LEASE_LOST')
        output=root/'artifacts'
        target=root/'frame-manifest.json'
        # Check before any processing write, not after the backend returns.
        if output.exists() or output.is_symlink() or target.exists() or target.is_symlink():
            raise ValueError('FRAME_OUTPUT_ALREADY_EXISTS')
        output.mkdir(mode=0o700)
        if output.resolve(strict=True).parent!=root:
            raise ValueError('FRAME_OUTPUT_OUTSIDE_WORKSPACE')
        # Import Pillow/processing only when a valid formal claim executes.
        from .frame_processing import process_set
        result=process_set(inputs,output,pixel_scale=.5)
        if cancel.is_set():raise RuntimeError('LEASE_LOST')
        result['requestId']=claim.request_id
        result['pipelinePolicy']='extract-alpha-pixel-half.v1'
        fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'w') as stream:
            stream.write(json.dumps(result,ensure_ascii=False,sort_keys=True))
        return (target,*(Path(p) for p in result['artifacts'].values()))
