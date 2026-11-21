"""Synthetic matte fixture; no model, PDS, or live workflow access."""
import importlib.util, json, subprocess, tempfile
from pathlib import Path
from PIL import Image
from workers.resident.backends.animation_native_png import build_native_png_manifest
from workers.resident.backends.frame_native_manifests import manifest_hash
spec=importlib.util.spec_from_file_location('workflow_postprocess',Path(__file__).with_name('postprocess-render.py'))
worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
with tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp);(root/'raw').mkdir()
    payloads=[]
    for i in range(2):
        img=Image.new('RGB',(48,48),'white'); img.paste((12,50,127),(12+i,12,33+i,36))
        p=root/'raw'/f'frame-{i:08d}.png';img.save(p);payloads.append(p.read_bytes())
    raw=build_native_png_manifest(dict(requestId='workflow.task.'+'b'*64,npcId='npc.fixture',generationId='a'*48,
        posePreset='idle',presetId='walk',direction='sw',sourceBindingHash='c'*64),payloads,frame_count=2,fps=24)
    def matte(root,*args):
        (root/'alpha').mkdir()
        for i,p in enumerate(sorted((root/'raw').glob('*.png'))):
            img=Image.open(p).convert('RGBA');a=Image.new('L',img.size);a.paste(255,(12+i,12,33+i,36));img.putalpha(a)
            img.save(root/'alpha'/p.name)
    claim=dict(taskId='workflow.task.'+'d'*64,npcId='npc.fixture')
    out=worker.process(root,claim,'walk','sw',raw,'e'*64,matte)
    alpha,pixel=out['alpha'],out['pixel']
    assert alpha['rawRequestId']==raw['requestId'] and pixel['requestId']==claim['taskId']
    assert pixel['canvas']['scale']==.5 and pixel['canvas']['width']<48
    assert pixel['pixelPolicy']['paletteScope']=='all-frames-in-direction'
    assert pixel['frames'][1]['sourceTimestamp']==1/24
    assert pixel['canvas']['unionBounds']==[12,12,34,36]
    assert type(pixel['canvas']['offsetX']) is int
    assert type(pixel['canvas']['pivot']['x']) is int
    assert type(pixel['canvas']['pivot']['y']) is int
    assert worker.wire_numbers({'nested':[1.0,{'negativeZero':-0.0,'fraction':.5,'flag':True}]})=={
        'nested':[1,{'negativeZero':0,'fraction':.5,'flag':True}]}
    # Mirror the production adapter's Python output → JSON.parse/Stringify
    # → PDS Python decode and canonical digest.
    wire=json.loads(subprocess.check_output(['node','-e',
        'process.stdout.write(JSON.stringify(JSON.parse(require("node:fs").readFileSync(0,"utf8"))))'],
        input=json.dumps(out).encode()))
    stored=wire['pixel']
    assert stored['normalizationHash']==manifest_hash(dict(canvas=stored['canvas'],pixelPolicy=stored['pixelPolicy']))
    assert stored['alphaManifestHash']==manifest_hash(wire['alpha'])

    for i,f in enumerate(pixel['frames']):
        assert f['rawAssetId']==raw['frames'][i]['assetId'] and f['alphaAssetId']==alpha['frames'][i]['alphaAssetId']
        png=Image.open(root/'assets'/f['assetId']).convert('RGBA')
        assert set(png.getchannel('A').getdata())=={0,255}
        assert len(set(png.getdata()))<=33
    try: worker.process(root,claim,'run','sw',raw,'e'*64,matte)
    except ValueError as e: assert str(e)=='POSTPROCESS_SOURCE_SCOPE'
    else: raise AssertionError('wrong motion accepted')
print('synthetic PNG fixture passed')
