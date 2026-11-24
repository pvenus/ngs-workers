import assert from 'node:assert/strict';
import test from 'node:test';
import {DatabaseSync} from 'node:sqlite';
import {chmodSync,mkdtempSync,readFileSync,realpathSync,rmSync,writeFileSync} from 'node:fs';
import {execFileSync,spawnSync} from 'node:child_process';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {fileURLToPath} from 'node:url';
import {canonical,sha} from './vendor/planner-f24d23a/envelope.mjs';
import {buildPropGeneratePlan,propKeys,propRef} from './vendor/prop-composite/prop-generate-plan.mjs';
import {runPropDerivationFromPath,nextPropDerivationFromPath} from './prop-derivation-worker.mjs';
import {renderPropInScratch} from './prop-derivation-provider.mjs';
import {verifyPropMedia} from './vendor/prop-composite/prop-package-contract.mjs';

const py=process.env.WORKFLOW_PROP_TEST_PYTHON??'python3';
const hasPillow=spawnSync(py,['-c','from PIL import Image'],{stdio:'ignore'}).status===0;
const program=fileURLToPath(new URL('./prop-derive.py',import.meta.url));
const oid='request.official.'+'2'.repeat(64);
const propId='ritual-bell';
const delay=[50,60,70];

function fixture(t,type='prop.generate',options={motion:'subtle'}){
 const root=realpathSync(mkdtempSync(join(tmpdir(),'prop-worker-fixture-')));
 t.after(()=>rmSync(root,{recursive:true,force:true}));
 const dbPath=join(root,'work.sqlite');
 writeFileSync(dbPath,'',{mode:0o600});chmodSync(dbPath,0o600);
 const db=new DatabaseSync(dbPath);t.after(()=>db.close());
 db.exec(`CREATE TABLE workflow_runs(official_request_id,project_id,request_type,schema_version,payload,payload_sha256,registered_at,state,updated_at,reason);
 CREATE TABLE workflow_tasks(task_id,official_request_id,task_key,task_type,worker_type,task_input,result_ref,updated_at,depends_on_task_id,status,retry_count);`);
 const schema={'prop.generate':'ngs.prop-generate.v1','prop.regenerate':'ngs.prop-regenerate.v1','prop.animation.generate':'ngs.prop-animation-generate.v1'}[type];
 const payload={schemaVersion:schema,projectId:'binari',propId,
  ...(type!=='prop.animation.generate'?{intent:'Ancient ritual bell',designContextSha256:'1'.repeat(64)}:{}),...options};
 const source=canonical(payload);
 db.prepare("INSERT INTO workflow_runs VALUES(?,?,?,?,?,?,1,'planned',1,NULL)")
   .run(oid,payload.projectId,type,payload.schemaVersion,source,sha(source));
 const tasks=buildPropGeneratePlan({officialRequestId:oid,requestType:type,payload});
 const by=Object.fromEntries(tasks.map(x=>[x.key,x]));
 for(const a of tasks)db.prepare("INSERT INTO workflow_tasks VALUES(?,?,?,?,?,?,NULL,2,?,'pending',0)")
   .run(a.id,oid,a.key,a.type,a.workerType,canonical(a.input),a.dependsOn);
 const complete=(key,hash)=>{
  const task=by[key],resultRef=propRef(key,hash);
  db.prepare("UPDATE workflow_tasks SET status='completed',result_ref=?,updated_at=3 WHERE task_id=?").run(resultRef,task.id);
  return {taskId:task.id,resultRef};
 };
 const realRender=async(job,binding,{signal}={})=>{
  const scratch=realpathSync(mkdtempSync(join(root,'render-')));
  try{return await renderPropInScratch(job,binding,{root:scratch,signal,execute(directory){
   execFileSync(py,['-B','-c',`
import json,runpy,sys
from pathlib import Path
from PIL import Image
code=runpy.run_path(sys.argv[1]);path=Path(sys.argv[2])
def matte(root,paths,key):
 out=[]
 for i,src in enumerate(paths):
  target=root/('fake-%d.png'%i)
  with Image.open(src) as image: rgba=image.convert('RGBA')
  rgba.putalpha(Image.frombytes('L',rgba.size,bytes(
      0 if r==0 and g==0 and b==0 else 220 for r,g,b,a in rgba.getdata())))
  rgba.save(target);out.append(target)
 return out
result=code['process'](path,json.loads((path/'job.json').read_bytes()),matte)
(path/'result.png').write_bytes(result)
`,program,directory]);
  }});}
  finally{rmSync(scratch,{recursive:true,force:true});}
 };
 return {type,payload,db,dbPath,by,complete,realRender,
  state:key=>db.prepare('SELECT status,result_ref,retry_count FROM workflow_tasks WHERE task_id=?').get(by[key].id),
  async run(key,pds,render=realRender){
   return await runPropDerivationFromPath({dbPath,taskId:by[key].id,officialRequestId:oid,pds,render});
  }};
}

function raw(width,height,index=0,mode='RGB'){
 return execFileSync(py,['-B','-c',`
import sys
from PIL import Image, ImageDraw
w,h,i=map(int,sys.argv[1:4]); mode=sys.argv[4]
im=Image.new(mode,(w,h));draw=ImageDraw.Draw(im)
draw.rectangle((w//4+i,h//4,3*w//4+i,3*h//4),fill=(120+i,30,190,253) if mode=='RGBA' else (120+i,30,190))
im.save(sys.stdout.buffer,format='PNG')
`,String(width),String(height),String(index),mode]);
}
const descriptor=(b,width,height)=>({sha256:sha(b),bytes:b.length,width,height,mediaType:'image/png'});
const canvas=(width,height)=>({width,height});
function setup(f,{tamper=()=>{},afterRender=()=>{}}={}){
 const stat=raw(80,64,0,'RGBA');const still=descriptor(stat,80,64);
 if(f.by[propKeys.base]){
  f.complete(propKeys.design,'5'.repeat(64));
  f.complete(propKeys.base,still.sha256);
 }
 const staged=new Map();
 const sourceFrames=[raw(100,72,0),raw(100,72,1),raw(100,72,1)];
 const manifest={schemaVersion:'ngs.prop-native-rgb-sequence.v1',officialRequestId:oid,
  taskId:f.by[propKeys.motion]?.id,propId,width:100,height:72,frameDurationsMs:delay,
  frames:sourceFrames.map((b,i)=>({index:i,sha256:sha(b),bytes:b.length,mediaType:'image/png'}))};
 if(f.by[propKeys.motion])f.complete(propKeys.motion,sha(canonical(manifest)));
 return {still,manifest,staged,pds:{
  async readPropDerivation(b){
   const media=b.key===propKeys.detail?still:undefined;
   const source={...b.source};
   const sibling=b.key===propKeys.pixel||b.key===propKeys.animPixel;
   const target=sibling?canvas(40,32):canvas(80,64);
   const trusted=b.animated?{...b.geometry,media:staged.get(b.role==='pixel'?propKeys.pixel:propKeys.detail)?.media}:null;
   const dto={source,canvas:target,...(b.animated?{geometry:trusted}:{}),
    ...(b.key===propKeys.animDetail?{manifest}:b.key===propKeys.detail?{media}:{media:staged.get(b.key===propKeys.pixel?propKeys.detail:propKeys.animDetail)?.media})};
   tamper(dto,b);
   return dto;
  },
  async fetchPropDerivationAsset(b,item,{purpose}={}){
   if(purpose==='geometry')return staged.get(b.role==='pixel'?propKeys.pixel:propKeys.detail)?.bytes;
   if(b.key===propKeys.detail)return stat;
   if(b.key===propKeys.animDetail)return sourceFrames[item.index];
   return staged.get(b.key===propKeys.pixel?propKeys.detail:propKeys.animDetail)?.bytes;
  },
  async stagePropDerivation(b,out){
   assert.deepEqual(out.source,b.source);
   await verifyPropMedia(out.bytes,out.media);
   await afterRender(b,out);
   staged.set(b.key,out);
   return {ok:true,resultRef:propRef(b.key,out.media.sha256)};
  },
 }};
}

test('still and subtle actual byte pipeline uses same-canvas timed variants including repeated frames',
 {skip:!hasPillow},async t=>{
  const f=fixture(t),{pds,staged}=setup(f);
  assert.equal((await f.run(propKeys.animDetail,pds)).state,'not_claimable');
  assert.equal(f.state(propKeys.animDetail).retry_count,0);
  for(const key of [propKeys.detail,propKeys.pixel,propKeys.animDetail,propKeys.animPixel]){
   const r=await f.run(key,pds);
   assert.equal(r.state,'completed',key+' '+JSON.stringify(r));
   assert.equal(r.resultRef,propRef(key,staged.get(key).media.sha256));
   assert.equal((await f.run(key,pds)).state,'not_claimable');
  }
  for(const [still,motion] of [[propKeys.detail,propKeys.animDetail],[propKeys.pixel,propKeys.animPixel]]){
   const a=staged.get(still).media,b=staged.get(motion).media;
   assert.deepEqual([b.width,b.height],[a.width,a.height]);assert.deepEqual(b.frameDurationsMs,delay);
   assert.equal(b.mediaType,'image/apng');
  }
  assert.equal(f.state(propKeys.publish).status,'pending');
});

test('changed source and wrong geometry pin do not publish (not merely correct-looking assets)',
 {skip:!hasPillow},async t=>{
  const f=fixture(t),{pds,staged}=setup(f,{tamper(dto,b){
   if(b.key===propKeys.animDetail)dto.geometry.resultRef=propRef(propKeys.detail,'d'.repeat(64));
  }});
  await f.run(propKeys.detail,pds);
  const r=await f.run(propKeys.animDetail,pds);
  assert.equal(r.state,'failed');
  assert.equal(staged.has(propKeys.animDetail),false);
  pds.fetchPropDerivationAsset=async()=>raw(80,64,2);
  assert.equal((await f.run(propKeys.pixel,pds)).state,'failed');
  assert.equal(staged.has(propKeys.pixel),false);
});

test('changed claim before stage prevents stale artifact write; uncertain ACK never auto repeats',
 {skip:!hasPillow},async t=>{
  const f=fixture(t,'prop.generate',{motion:'still'}),{pds,staged}=setup(f);
  const failed=await f.run(propKeys.detail,pds,async(...args)=>{
   const result=await f.realRender(...args);
   f.db.prepare("UPDATE workflow_tasks SET updated_at=updated_at+1 WHERE task_id=?").run(f.by[propKeys.detail].id);
   return result;
  });
  assert.equal(failed.state,'requires_reconciliation');
  assert.equal(staged.size,0);
  f.db.prepare("UPDATE workflow_tasks SET status='pending' WHERE task_id=?").run(f.by[propKeys.detail].id);
  const stage=pds.stagePropDerivation;
  pds.stagePropDerivation=async(...args)=>{await stage(...args);throw Error('lost response');};
  assert.equal((await f.run(propKeys.detail,pds)).state,'requires_reconciliation');
  assert.equal(staged.size,1);
  assert.equal((await f.run(propKeys.detail,pds)).state,'not_claimable');
});

test('append reads prior package geometry without creating replacement stills',
 {skip:!hasPillow},async t=>{
  const original=fixture(t,'prop.generate',{motion:'still'}),r=setup(original);
  for(const key of [propKeys.detail,propKeys.pixel])assert.equal((await original.run(key,r.pds)).state,'completed');
  const packagePin={revision:3,sha256:'a'.repeat(64)};
  const f=fixture(t,'prop.animation.generate',{source:packagePin,motionId:'ring-once',prompt:'A short ring'});
  const {pds,staged}=setup(f);
  const originalMedia=key=>r.staged.get(key==='pixel'?propKeys.pixel:propKeys.detail);
  const read=pds.readPropDerivation.bind(pds),fetch=pds.fetchPropDerivationAsset.bind(pds);
  pds.readPropDerivation=async b=>({...(await read(b)),geometry:{...packagePin,media:originalMedia(b.role).media}});
  pds.fetchPropDerivationAsset=async (b,item,opts)=>opts?.purpose==='geometry'?originalMedia(b.role).bytes:fetch(b,item,opts);
  const pre=pds.readPropDerivation;
  pds.readPropDerivation=async b=>{const dto=await pre(b);dto.geometry.revision++;return dto;};
  assert.equal((await f.run(propKeys.animDetail,pds)).state,'failed');
  pds.readPropDerivation=pre;
  // A rejected attempt never consumed the old package. Explicit fixture reset
  // models a later still-current claim, without changing the immutable pin.
  f.db.prepare("UPDATE workflow_tasks SET status='pending' WHERE task_id=?").run(f.by[propKeys.animDetail].id);
  for(const key of [propKeys.animDetail,propKeys.animPixel])assert.equal((await f.run(key,pds)).state,'completed');
  assert.equal(f.by[propKeys.detail],undefined);
  assert.equal(f.by[propKeys.pixel],undefined);
  assert.deepEqual([...staged.keys()],[propKeys.animDetail,propKeys.animPixel]);
  assert.equal(original.state(propKeys.pixel).result_ref,propRef(propKeys.pixel,originalMedia('pixel').media.sha256));
});

test('animated scanline filtering fits a strict byte budget without altering alpha, timing or decoded RGBA',
 {skip:!hasPillow},async()=>{
  const output=execFileSync(py,['-B','-c',`
import io,runpy,sys,zlib
from PIL import Image
M=runpy.run_path(sys.argv[1]);encode=M['encode']
w,h=320,240
alpha=lambda x,y: 0 if x<10 or y<10 else 123 if x<12 else 255
frames=[]
for j in range(4):
 raw=bytes(v for y in range(h) for x in range(w) for v in (
  (x*7+y*13+j*31)%256, (x+y*j)%256, (x*3+y*11)%256, alpha(x,y)))
 frames.append(Image.frombytes('RGBA',(w,h),raw))
def old(frame):
 data=frame.tobytes()
 return zlib.compress(b''.join(b'\\0'+data[i:i+w*4] for i in range(0,len(data),w*4)),6)
prior=sum(len(old(frame)) for frame in frames)
times=[83,84,83,83]
first=encode(frames,times)
assert len(first)+1024<prior,(len(first),prior)
encode.__globals__['MAX_BYTES']=(len(first)+prior)//2
assert len(encode(frames,times))==len(first)
with Image.open(io.BytesIO(first)) as decoded:
 assert decoded.n_frames==len(times)
 for i,frame in enumerate(frames):
  decoded.seek(i)
  assert decoded.info['duration']==times[i]
  assert decoded.tobytes()==frame.tobytes()
sys.stdout.buffer.write(first)
`,program]);
  const sha256=sha(output);
  await verifyPropMedia(output,{assetId:sha256+'.png',sha256,bytes:output.length,
   width:320,height:240,mediaType:'image/apng',frameDurationsMs:[83,84,83,83]});
 });

test('worker reports exact declared failure stage and fixed child classification, never private message',
 {skip:!hasPillow},async t=>{
  for(const [payload,phase,code] of [
   ['PROP_DERIVATION_OUTPUT_BUDGET','render','PROP_DERIVATION_OUTPUT_BUDGET'],
   ['/private/hidden provider traceback','render','PROP_DERIVATION_RENDER_FAILED'],
  ]){
   const f=fixture(t,'prop.generate',{motion:'still'}),{pds,staged}=setup(f);
   const r=await f.run(propKeys.detail,pds,async()=>{throw Error(payload);});
   assert.deepEqual(r,{state:'failed',taskId:f.by[propKeys.detail].id,phase,code});
   assert.equal(staged.size,0);
   assert.equal(f.state(propKeys.detail).result_ref,null);
  }
 });

test('static native RGBA keeps petal opacity and soft edges; invalid cutouts fail without matting',
 {skip:!hasPillow},async()=>{
  execFileSync(py,['-B','-c',`
import hashlib,io,runpy,sys,tempfile
from pathlib import Path
from PIL import Image,ImageDraw
process=runpy.run_path(sys.argv[1])['process']
root=Path(tempfile.mkdtemp())
try:
 (root/'raw').mkdir()
 source=root/'raw'/'frame-00000000.png'; matte_calls=[]
 def describe(raw,**extra):
  return {'width':64,'height':48,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),**extra}
 def fallback(root,paths,key):
  matte_calls.append((paths,key))
  raise AssertionError('Static matting must never be called')
 def run_detail(im,*,coded=None,corrupt=False):
  im.save(source)
  if corrupt:source.write_bytes(source.read_bytes()[:-17])
  original=source.read_bytes()
  spec={'kind':'detail','canvas':{'width':64,'height':48},'frameDurationsMs':[],
        'frames':[describe(original,index=0)],'sourceHash':'a'*64}
  try:
   encoded=process(root,spec,fallback)
  except ValueError as err:
   assert coded == str(err),(coded,str(err))
   encoded=None
  else:
   assert coded is None
  assert source.read_bytes()==original
  return encoded
 native=Image.new('RGBA',(64,48),(11,44,15,0))
 ImageDraw.Draw(native).rectangle((12,12,40,30),fill=(145,99,42,253))
 native.putpixel((11,13),(145,99,42,23))
 detail=run_detail(native)
 assert len(matte_calls)==0
 out=Image.open(io.BytesIO(detail))
 assert out.getchannel('A').tobytes()==native.getchannel('A').tobytes()
 assert out.getpixel((20,20))==(145,99,42,253)
 assert out.getpixel((11,13))==(145,99,42,23)
 assert out.getpixel((0,0))==(0,0,0,0)
 (root/'source.png').write_bytes(detail)
 spec={'kind':'pixel','canvas':{'width':32,'height':24},'frameDurationsMs':[],'media':describe(detail)}
 small=Image.open(io.BytesIO(process(root,spec,fallback)))
 assert small.getpixel((10,10))[3]==255 and small.getpixel((0,0))[3]==0
 # Header can declare RGBA even when blank, opaque, fake painted, clipped,
 # or there are only translucent pixels and no solid foreground.
 invalid='PROP_DERIVATION_STATIC_NATIVE_ALPHA_REQUIRED'
 for mode,alpha in [('RGB',None),('RGBA',0),('RGBA',255),('RGBA',42)]:
  base=Image.new(mode,(64,48),(11,44,15) if alpha is None else (11,44,15,alpha))
  if alpha==255: ImageDraw.Draw(base).rectangle((10,10,20,20),fill=(180,180,180,255))
  run_detail(base,coded=invalid)
 cropped=native.copy();ImageDraw.Draw(cropped).line((0,5,0,43),fill=(145,99,42,253))
 run_detail(cropped,coded=invalid)
 no_solid=native.copy()
 no_solid.putalpha(native.getchannel('A').point(lambda a:42 if a else 0))
 run_detail(no_solid,coded=invalid)
 run_detail(native,coded='PROP_DERIVATION_STATIC_SOURCE_PNG_INVALID',corrupt=True)
 assert len(matte_calls)==0
finally:
 import shutil
 shutil.rmtree(root)
`,program]);
 });
