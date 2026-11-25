import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {readFile,mkdtemp,mkdir,rm,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {test} from 'node:test';
import {createPropDerivationRenderer} from './prop-derivation-provider.mjs';

const python=process.env.WORKFLOW_PROP_TEST_PYTHON;
const fake=`import cv2, numpy as np, sys
from pathlib import Path
a = sys.argv
w,h = int(a[a.index('--width') + 1]), int(a[a.index('--height') + 1])
assert (w,h) in ((1280,768),(1536,1024))
assert a[a.index('--hann-chunk') + 1] == ('4' if w == 1280 else '1')
assert a[a.index('--hann-stride') + 1] == ('2' if w == 1280 else '1')
assert a[a.index('--precision') + 1] == 'fp32'
assert '--image-sequence' in a and a[-1] == 'cutout'
assert a[a.index('--despill-strength') + 1] == '0.10'
assert not any(x.startswith('--hint') for x in a)
raw = Path(a[a.index('--input') + 1])
out = Path(a[a.index('--output-dir') + 1]) / 'cutout'
out.mkdir(parents=True)
for i, src in enumerate(sorted(raw.glob('*.png'))):
    frame = cv2.imread(str(src))
    assert frame.shape == (h,w,3)
    frame = frame.astype(np.uint16) * 257
    alpha = (frame[:, :, 2] > frame[:, :, 1]).astype(np.uint16) * 65535
    assert cv2.imwrite(str(out / f'{i:06d}.png'), np.dstack((frame, alpha)))
`;

test('resident prop renderer defaults to CorridorKey for raw prop bases and prop animation', {skip:!python}, async()=>{
 const root=await mkdtemp(join(tmpdir(),'prop-corridor-test-'));
 try{
  const configPath=join(root,'config.json'),modelPinsPath=join(root,'pins.json'),corridorConfigPath=join(root,'corridor.json');
  const model=join(root,'models'),cli=join(root,'cli'),src=join(root,'input.png');
  await mkdir(model);
  await writeFile(cli,`#!${python}\n${fake}`,{mode:0o700});
  const {spawnSync}=await import('node:child_process');
  const fixture=spawnSync(python,['-B','-c',`from PIL import Image
import sys
im=Image.new('RGB',(512,352),(0,255,0))
im.paste((225,70,0),(100,50,400,330))
im.save(sys.argv[1])`,src]);
  assert.equal(fixture.status,0,fixture.stderr?.toString());
  const cfg={FRAME_ALPHA_PYTHON:python,FRAME_ALPHA_MODULE_ROOT:root,FRAME_ALPHA_MATTING_MODEL:model,
    FRAME_ALPHA_TRACKING_MODEL:model};
  await writeFile(corridorConfigPath,JSON.stringify({PROP_CORRIDOR_V2_CLI:cli,PROP_CORRIDOR_V2_MODEL_ROOT:model}));
  await writeFile(configPath,JSON.stringify(cfg));
  await writeFile(modelPinsPath,JSON.stringify({m:{path:model}}));
  const options={configPath,modelPinsPath,corridorConfigPath,workerSource:root,samSource:root};
  const render=await createPropDerivationRenderer(options);
  const data=await readFile(src),frame={bytes:data.length,sha256:createHash('sha256').update(data).digest('hex'),width:512,height:352};
  const job={source:{resultRef:'stage.'+'1'.repeat(64)},canvas:{width:512,height:352},
   buffers:Array(4).fill(data),frames:Array.from({length:4},(_,index)=>({...frame,index})),
   frameDurationsMs:[83,83,84,83],renderProfile:'candle-chroma-headroom-v1'};
  const out=await render({...job,renderProfile:undefined},{role:'detail'});
  assert.equal(out.readUInt32BE(16),512);
  assert.equal(out.readUInt32BE(20),352);
  assert.equal(out.readUInt32BE(41),4); // acTL: one output per input frame
  const still={...job,buffers:[data],frames:undefined,media:frame,frameDurationsMs:[],renderProfile:undefined};
  const stillResult=await render(still,{role:'detail'});
  assert.equal(stillResult.readUInt8(25),6); // standard RGBA
  assert.equal(stillResult.toString('ascii',37,41),'IDAT'); // still PNG, not APNG
  await rm(cli);
  await assert.rejects(render(job,{role:'detail'}),{message:'PROP_DERIVATION_CORRIDOR_CONFIG'});
  await assert.rejects(render({...job,renderProfile:undefined},{role:'detail'}),{message:'PROP_DERIVATION_CORRIDOR_CONFIG'});
  await rm(corridorConfigPath);
  const original=await createPropDerivationRenderer(options);
  await assert.rejects(original(job,{role:'detail'}),{message:'PROP_DERIVATION_CORRIDOR_CONFIG'});
  const legacy=join(root,'legacy.png');
  assert.equal(spawnSync(python,['-B','-c',`from PIL import Image
import sys
im=Image.new('RGBA',(512,352),(0,0,0,0))
im.paste((225,70,0,255),(100,50,400,330))
im.save(sys.argv[1])`,legacy]).status,0);
  const rgba=await readFile(legacy),info={...frame,bytes:rgba.length,sha256:createHash('sha256').update(rgba).digest('hex')};
  assert.ok((await original({...still,media:info,buffers:[rgba]},{role:'detail'})).length>57);
 }finally{await rm(root,{recursive:true,force:true});}
});
