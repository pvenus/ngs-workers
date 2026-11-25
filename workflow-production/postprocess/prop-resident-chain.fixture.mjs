// Scratch fixture driver: CLI arguments are supplied by actual backend test
// class, loopback ports are ephemeral, and no real model is imported or run.
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {mkdtemp,rm} from 'node:fs/promises';
import {request} from 'node:http';
import {createPropI2VInputFraming} from '../animation/prop-i2v-input-framing.mjs';
import {DatabaseSync} from 'node:sqlite';
import {choosePropAnimation,executePropAnimation,propAnimationPorts} from '../animation/prop-animation-worker.mjs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {localPorts as documentPorts,requestLocalJson} from '../document/local-ports.mjs';
import {localPorts as imagePorts,requestLocalJson as imageJson} from '../image/local-ports.mjs';
import {nextPropBaseFromPath,runPropBaseFromPath} from '../image/prop-base-worker.mjs';
import {runPropDesignFromPath} from '../document/prop-design-worker.mjs';
import {propStagePorts} from './prop-stage-ports.mjs';
import {nextPropDerivationFromPath,runPropDerivationFromPath} from './prop-derivation-worker.mjs';
import {nextPropPackageFromPath,runPropPackageFromPath} from './prop-package-worker.mjs';
import {renderPropInScratch} from './prop-derivation-provider.mjs';
import {propTaskId,propKeys} from './vendor/prop-composite/prop-generate-plan.mjs';

const [dbPath,officialRequestId,rawPort,operation,label,profile]=process.argv.slice(2);
assert.ok(dbPath&&officialRequestId&&/^[1-9]\d{3,4}$/.test(rawPort)&&['prop.generate','prop.regenerate','prop.animation.generate'].includes(operation)&&label);
const port=Number(rawPort);
const python=process.env.WORKFLOW_PROP_TEST_PYTHON??'python3';
const taskId=key=>propTaskId(officialRequestId,key);
const program=fileURLToPath(new URL('./prop-derive.py',import.meta.url));
const docPds=documentPorts({transport:args=>requestLocalJson({...args,port})});
const transport=(path,{body,signal,type,limit,timeout})=>new Promise((resolve,reject)=>{
 const data=body===undefined?null:Buffer.from(JSON.stringify(body));
 const abort=signal?AbortSignal.any([signal,AbortSignal.timeout(timeout)]):AbortSignal.timeout(timeout);
 const req=request({hostname:'127.0.0.1',port,path,method:data?'POST':'GET',agent:false,signal:abort,
  headers:{Accept:type,...(data?{'Content-Type':'application/json','Content-Length':data.length}:{})}},rsp=>{
  if(rsp.statusCode!==200||String(rsp.headers['content-type']).split(';')[0]!==type){
   rsp.resume();reject(Error('fixture_http_'+rsp.statusCode));return;
  }
  const chunks=[];let size=0;
  rsp.on('data',chunk=>{size+=chunk.length;if(size>limit)req.destroy();else chunks.push(chunk);});
  rsp.on('error',reject);rsp.on('aborted',()=>reject(Error('abort')));
  rsp.on('end',()=>resolve(Buffer.concat(chunks)));
 });
 req.on('error',reject);req.end(data);
});
const pds=propStagePorts({transport});
assert.equal(await pds.ready(),true);
const isAppend=operation==='prop.animation.generate';
const isChroma=profile!==undefined;
assert.ok(!isChroma||isAppend&&profile==='candle-chroma-headroom-v1');
if(!isAppend){
 const result=await runPropDesignFromPath({dbPath,officialRequestId,taskId:taskId(propKeys.design),operation:operation+'.design',
  pds:docPds,render:async input=>{
   assert.equal(input.sources.length,2);
   return {title:label,appearancePrompt:'종이 등롱',motionPrompt:null,interaction:{
    pixelOpen:{xPermille:50,yPermille:100,widthPermille:500,heightPermille:250},detailTrigger:null}};
  }});
 assert.equal(result.state,'completed',JSON.stringify(result));

 const base=execFileSync(python,['-B','-c',`
import sys
from PIL import Image,ImageDraw
im=Image.new('RGB',(40,32),(0,255,0))
ImageDraw.Draw(im).rectangle((10,8,29,22),fill=(int(sys.argv[1]),55,94))
im.save(sys.stdout.buffer,format='PNG')
`,operation==='prop.generate'?'90':'180']);

 const imagePds=imagePorts({transport:args=>imageJson({...args,port})});
 const image=nextPropBaseFromPath(dbPath);
 assert.equal(image.taskId,taskId(propKeys.base));
 const baseResult=await runPropBaseFromPath({dbPath,taskId:taskId(propKeys.base),officialRequestId,operation:operation+'.base',
  pds:imagePds,renderProp:async input=>{
   assert.equal(input.state,'base');assert.equal(input.lane,'detail');
   return base;
  }});
 assert.equal(baseResult.state,'completed',JSON.stringify(baseResult));
}else{
 const db=new DatabaseSync(dbPath);const rawPorts=propAnimationPorts(async (path,opts={})=>{
  const bytes=await transport(path,{...opts,type:opts.binary?'image/png':'application/json',
   limit:opts.maxBinaryBytes??opts.maxJsonBytes??4096,timeout:opts.timeout??3000});
  return opts.binary?bytes:JSON.parse(bytes);
 });
 try{
  assert.equal(await rawPorts.ready(),true);
  const raw=choosePropAnimation(dbPath);
  assert.deepEqual({...raw},{officialRequestId,taskId:taskId(propKeys.motion)});
  const result=await executePropAnimation(db,raw,new AbortController().signal,{ports:rawPorts,prepareInput:createPropI2VInputFraming(python),renderer:{async render(job){
   assert.ok(job.source.length>50);
   if(isChroma)assert.equal(job.motionPrompt,'Only the flame flickers');
   else{
    assert.match(job.motionPrompt,/#00FF00/);
    assert.ok(job.motionPrompt.endsWith('Requested prop motion: Only the flame flickers'));
   }
   assert.deepEqual([job.source.readUInt32BE(16),job.source.readUInt32BE(20)],[512,352]);
   const frames=[60,100,160].map(color=>execFileSync(python,['-B','-c',`
import sys
from PIL import Image,ImageDraw
im=Image.new('RGB',(512,352),(0,255,0))
ImageDraw.Draw(im).rectangle((128,110,383,310),fill=(255,int(sys.argv[1]),8))
im.save(sys.stdout.buffer,format='PNG')
`,String(color)]));
   return {frames,width:512,height:352,fps:12};
  }}});
  assert.equal(result.state,'completed',JSON.stringify(result));
  assert.equal(result.frameCount,3);
 }finally{db.close();}
}

const render=async(job,binding,{signal}={})=>{
 const scratch=await mkdtemp(join(tmpdir(),'prop-real-processor-fixture-'));
 try{return await renderPropInScratch(job,binding,{root:scratch,signal,execute(directory){
  execFileSync(python,['-B','-c',`
import json,runpy,sys
from pathlib import Path
from PIL import Image
sys.path.insert(0,str(Path(sys.argv[1]).parent))
code=runpy.run_path(sys.argv[1]); root=Path(sys.argv[2])
spec=json.loads((root/'job.json').read_bytes())
corridor=spec['kind']=='detail'
if corridor:
 import cv2,numpy as np
 from prop_corridor import prepare
 prepare(root)
 out=root/'corridor'/'cutout'
 out.mkdir(parents=True)
 for i,path in enumerate(sorted((root/'corridor-input').glob('*.png'))):
  rgb=cv2.imread(str(path)).astype(np.uint16)*257
  green=rgb[:,:,1]>np.maximum(rgb[:,:,0],rgb[:,:,2])
  rgba=np.dstack((rgb,np.where(green,0,65535).astype(np.uint16)))
  assert cv2.imwrite(str(out/f'{i:06d}.png'),rgba)
def forbidden(*args):
 raise AssertionError('legacy matting is not part of this chain')
(root/'result.png').write_bytes(code['process'](root,spec,forbidden,corridor=corridor))
`,program,directory]);
 }});}finally{await rm(scratch,{recursive:true,force:true});}
};
const derived={};
const mode={stillOnly:true,appendEnabled:isAppend};
for(const key of (isAppend?[propKeys.animDetail,propKeys.animPixel]:[propKeys.detail,propKeys.pixel])){
 const selected=nextPropDerivationFromPath(dbPath,mode);
 assert.deepEqual(selected,{officialRequestId,taskId:taskId(key)});
 const r=await runPropDerivationFromPath({dbPath,...selected,pds,...mode,render});
 assert.equal(r.state,'completed',JSON.stringify(r));derived[key]=r.resultRef;
}
const selected=nextPropPackageFromPath(dbPath,mode);
assert.deepEqual(selected,{officialRequestId,taskId:taskId(propKeys.publish)});
const published=await runPropPackageFromPath({dbPath,...selected,pds,...mode});
assert.equal(published.state,'completed',JSON.stringify(published));
assert.equal(nextPropPackageFromPath(dbPath,mode),null);
console.log(JSON.stringify({published,derived}));
