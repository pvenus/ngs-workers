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

function raw(width,height,index=0){
 return execFileSync(py,['-B','-c',`
import sys
from PIL import Image, ImageDraw
w,h,i=map(int,sys.argv[1:])
im=Image.new('RGB',(w,h));draw=ImageDraw.Draw(im)
draw.rectangle((w//4+i,h//4,3*w//4+i,3*h//4),fill=(120+i,30,190))
im.save(sys.stdout.buffer,format='PNG')
`,String(width),String(height),String(index)]);
}
const descriptor=(b,width,height)=>({sha256:sha(b),bytes:b.length,width,height,mediaType:'image/png'});
const canvas=(width,height)=>({width,height});
function setup(f,{tamper=()=>{},afterRender=()=>{}}={}){
 const stat=raw(80,64);const still=descriptor(stat,80,64);
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
