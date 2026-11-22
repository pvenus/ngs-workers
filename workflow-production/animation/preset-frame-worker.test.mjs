import test from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {chmodSync,existsSync,mkdtempSync,realpathSync,readFileSync,rmSync,writeFileSync,mkdirSync} from 'node:fs';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {crc32,deflateSync} from 'node:zlib';
import {
 claimPresetFrames,choosePresetFrames,framesKey,framesType,imageType,imageKey,
 requestType,requestSchema,materializeRead,executePresetFrames,presetFramePorts,
 DefinitePresetWriteRejection,presetPath,presetCapability,
} from './preset-frame-worker.mjs';
import {directions,sha256,width,height,makeFrameBundle,maxHeaderBytes,validTiming} from './preset-frame-artifact.mjs';

const id='request.official.4ec9fcca84e0839dfb2b37142f360737b5b275ca71c56e53e06dc3e14ef35181';
const keyId=key=>'workflow.task.'+sha256(JSON.stringify([id,key]));
const sourcePresetId='walk',variantPresetId='walk-stylized-2d',sourcePresetHash='a'.repeat(64);
const hash=ch=>ch.repeat(64);
const packed=bytes=>({sha256:sha256(bytes),base64:bytes.toString('base64'),bytes:bytes.length});
const pngHeader=Buffer.from('89504e470d0a1a0a0000000d4948445200000300000001b00802000000000000000000','hex');
const mp4Header=Buffer.from('000000146674797069736f6d0000000069736f6d000000146d646174000000000000000000000000','hex');
const signal=new AbortController().signal;
const target=d=>({taskId:keyId(framesKey(d)),officialRequestId:id,type:framesType,key:framesKey(d)});
const common={sourcePresetId,variantPresetId};
const canonical=v=>JSON.stringify(Object.fromEntries(Object.entries(v).sort(([a],[b])=>a.localeCompare(b))));

function fixture(t){
 const dir=realpathSync(mkdtempSync(join(tmpdir(),'preset-2d-worker-fixture-')));
 const file=join(dir,'workflow.sqlite'),db=new DatabaseSync(file);
 chmodSync(file,0o600);
 t.after(()=>{db.close();rmSync(dir,{recursive:true,force:true});});
 db.exec(`CREATE TABLE workflow_runs(official_request_id TEXT PRIMARY KEY, project_id TEXT,
 request_type TEXT,schema_version TEXT,payload TEXT,payload_sha256 TEXT,state TEXT,reason TEXT,registered_at INTEGER);
 CREATE TABLE workflow_tasks(task_id TEXT PRIMARY KEY,official_request_id TEXT,worker_type TEXT,task_type TEXT,
 task_key TEXT,task_input TEXT,status TEXT,result_ref TEXT,depends_on_task_id TEXT,updated_at INTEGER,retry_count INTEGER)`);
 const body=canonical({schemaVersion:requestSchema,projectId:'binari',...common,label:'걷기 2D'});
 assert.equal(sha256(body),'bab3b0dca09fe9cb4889eeb154053e98613717cf8aef7a7603684808ddf70542');
 db.prepare("INSERT INTO workflow_runs VALUES (?,?,?,?,?,?, 'planned', NULL, 1795290000000)")
  .run(id,'binari',requestType,requestSchema,body,sha256(body));
 const add=(key,type,worker,input,depends)=>
  db.prepare("INSERT INTO workflow_tasks VALUES(?,?,?,?,?,?,'pending',NULL,?,1,0)")
    .run(keyId(key),id,worker,type,key,canonical(input),depends);
 add(imageKey,imageType,'image',common,null);
 for(const d of directions)add(framesKey(d),framesType,'animation',{...common,direction:d},keyId(imageKey));
 add('motion_2d_n','motion-preset.convert-2d.clip','animation',{...common,direction:'n'},keyId(imageKey));
 add('motion_2d_finalize','motion-preset.convert-2d.finalize','animation',{...common,dependsOnTaskIds:[]},null);
 const set=(key,result,status='completed')=>db.prepare('UPDATE workflow_tasks SET status=?,result_ref=? WHERE task_key=?').run(status,result,key);
 const row=key=>db.prepare('SELECT status,result_ref AS resultRef FROM workflow_tasks WHERE task_key=?').get(key);
 return {file,db,row,set,dir,image:()=>set(imageKey,'motion.2d.image.'+sha256(pngHeader))};
}

function readClip(d){
 const source=packed(Buffer.concat([mp4Header,Buffer.from(d)]));
 return {direction:d,sourcePresetHash,reference:{
  schemaVersion:'ngs.motion-preset.v1',presetId:sourcePresetId,posePreset:'idle',label:'걷기',policy:{
   framingPolicy:{schemaVersion:'ngs.animation-motion-safe-frame.v1',maxHeightRatio:.9,maxWidthRatio:.8,offsetY:0,allowUpscale:false},
   equipmentPolicy:{heldItems:'',weaponState:'',excludedItems:[]},motionInstruction:'Follow the cycle.'},
  direction:d,assetId:source.sha256+'.mp4',...source,frameCount:4,fps:24,cycleCount:1,
  sourceVersion:hash('b'),presetHash:sourcePresetHash},image:packed(pngHeader)};
}
function parseWire(wire){
 const n=wire.readUInt32BE(0);
 assert(n>0&&n<=maxHeaderBytes);
 const raw=wire.subarray(4+n),head=JSON.parse(wire.subarray(4,4+n).toString('utf8'));
 assert.deepEqual(Object.keys(head).sort(),['claim','frames']);
 let offset=0;
 for(const f of head.frames){
  assert.deepEqual(Object.keys(f).sort(),['bytes','sha256']);
  assert.equal(sha256(raw.subarray(offset,offset+f.bytes)),f.sha256);
  offset+=f.bytes;
 }
 assert.equal(offset,raw.length);
 return {...head,raw};
}
const finalHash=(direction,frames)=>sha256(JSON.stringify({direction,frames,height,sourcePresetHash,width}));
function ports(d,{handoff}={}){
 return {read:async()=>readClip(d),handoff:handoff??(async wire=>{
  const {frames}=parseWire(wire);
  return {ok:true,resultRef:'motion.2d.frames.'+d+'.'+finalHash(d,frames)};
 })};
}

function renderFixture(dir){
 const frames=['00000.png','00001.png','00002.png','00003.png'];
 const local=join(dir,'wan','frames');mkdirSync(local,{recursive:true});
 const png=realFixturePNG();
 for(const name of frames)writeFileSync(join(local,name),png);
 return {width,height,fps:24,frames,frameHashes:frames.map(()=>sha256(png))};
}

test('one shared generated image gates exactly eight GPU frame bundles; no legacy clip or finalizer',async t=>{
 const f=fixture(t);
 assert.equal(choosePresetFrames(f.file),undefined);
 assert.equal(claimPresetFrames(f.db,target('e')),null);
 f.image();
 assert.equal(choosePresetFrames(f.file).type,framesType);
 const images=[],sources=[];
 for(const d of directions){
  const task=target(d);
  let scratch;
  const result=await executePresetFrames(f.db,task,signal,{pds:ports(d),
   native:async dir=>{
    scratch=dir;
    const job=JSON.parse(readFileSync(join(dir,'job.json')));
    assert.equal(job.frameCount,4);assert.equal(job.fps,24);assert.equal(job.referenceType,'portrait');
    assert.match(job.motionPrompt,/single-cycle directional/);
    assert(!job.motionPrompt.includes('걷기 2D'));
    images.push(sha256(readFileSync(join(dir,'portrait.png'))));
    sources.push(sha256(readFileSync(join(dir,'motion.mp4'))));
    return renderFixture(dir);
   }});
  assert.equal(result.state,'completed',JSON.stringify(result));
  assert.match(result.resultRef,new RegExp('^motion[.]2d[.]frames[.]'+d+'[.][a-f0-9]{64}$'));
  assert.equal(f.row(task.key).status,'completed');
  assert.equal(f.row(task.key).resultRef,result.resultRef);
  assert.equal(existsSync(scratch),false);
  assert.equal(claimPresetFrames(f.db,task),null);
 }
 assert.equal(new Set(images).size,1);
 assert.deepEqual(sources,directions.map(d=>readClip(d).reference.sha256));
 assert.equal(new Set(sources).size,8);
 assert.equal(choosePresetFrames(f.file),undefined);
 for(const [key,type] of [['motion_2d_n','motion-preset.convert-2d.clip'],['motion_2d_finalize','motion-preset.convert-2d.finalize']]){
  assert.equal(f.row(key).status,'pending');
  assert.throws(()=>claimPresetFrames(f.db,{taskId:keyId(key),officialRequestId:id,key,type}),/PRESET_2D_TASK_KIND/);
 }
});

test('rejects source drift, different image, wrong timing/direction before renderer',t=>{
 const f=fixture(t);f.image();
 const bound=claimPresetFrames(f.db,target('w'));
 const read=readClip('w');
 assert.equal(materializeRead(bound,read).source.length,read.reference.bytes);
 assert.throws(()=>materializeRead(bound,{...read,sourcePresetHash:hash('1')}),/PRESET_2D_CLIP_REFERENCE_SEMANTICS/);
 assert.throws(()=>materializeRead(bound,{...read,direction:'e'}),/PRESET_2D_CLIP_SOURCE/);
 assert.throws(()=>materializeRead(bound,{...read,image:{...read.image,sha256:hash('3')}}),/PRESET_2D_CLIP_SHARED_IMAGE_BINDING/);
 assert.throws(()=>materializeRead(bound,{...read,reference:{...read.reference,fps:0}}),/PRESET_2D_CLIP_REFERENCE_SEMANTICS/);
 assert.throws(()=>materializeRead(bound,{...read,reference:{...read.reference,base64:packed(pngHeader).base64}}),/PRESET_2D_ASSET_/);
 assert.equal(validTiming({frameCount:1000,fps:30}),true);
 assert.equal(validTiming({frameCount:1001,fps:30}),false);
});

test('lost binary handoff ACK remains running; definite scoped reject follows retry rules',async t=>{
 const f=fixture(t);f.image();
 const go=(d,handoff)=>executePresetFrames(f.db,target(d),signal,{pds:ports(d,{handoff}),native:renderFixture});
 assert.equal((await go('n',async()=>{throw Error('NETWORK');})).state,'requires_reconciliation');
 assert.equal(f.row(framesKey('n')).status,'running');
 assert.equal(claimPresetFrames(f.db,target('n')),null);
 assert.equal((await go('ne',async()=>{throw new DefinitePresetWriteRejection('MOTION_2D_REJECTED');})).state,'failed');
 assert.equal(f.row(framesKey('ne')).status,'failed');
 assert.equal((await go('e',async()=>({ok:true,resultRef:'motion.2d.frames.s.'+hash('e')}))).state,'requires_reconciliation');
});

test('binary PDS capability, body and exact definitive rejection handling',async()=>{
 let behavior={enabled:true,schemaVersion:presetCapability};
 const body=Buffer.from('test packet without JSON wrapping');
 const p=presetFramePorts(async (path,options)=>{
  assert.equal(path,presetPath+'/frames');
  assert(options.maxJsonBytes>0);
  if(options.onRejected){
   assert.equal(options.rawBody,body);assert.equal(options.body,undefined);
   options.onRejected(behavior);
  }
  return behavior;
 });
 assert(await p.ready(signal));
 behavior={enabled:true,schemaVersion:presetCapability,injected:true};
 assert.equal(await p.ready(signal),false);
 behavior={error:'MOTION_2D_REJECTED'};
 await assert.rejects(p.handoff(body),DefinitePresetWriteRejection);
});

function chunk(name,payload){
 const head=Buffer.alloc(8),tail=Buffer.alloc(4);
 head.writeUInt32BE(payload.length);head.write(name,4);
 tail.writeUInt32BE(crc32(Buffer.concat([head.subarray(4),payload])));
 return Buffer.concat([head,payload,tail]);
}

function realFixturePNG(){
 const ihdr=Buffer.alloc(13);
 ihdr.writeUInt32BE(width,0);ihdr.writeUInt32BE(height,4);ihdr[8]=8;ihdr[9]=2;
 return Buffer.concat([pngHeader.subarray(0,8),chunk('IHDR',ihdr),
  chunk('IDAT',deflateSync(Buffer.alloc((width*3+1)*height))),chunk('IEND',Buffer.alloc(0))]);
}

test('lossless scratch packet binds exact order, sizes and canonical manifest digest',async t=>{
 const dir=realpathSync(mkdtempSync(join(tmpdir(),'preset-2d-frame-packet-fixture-')));
 t.after(()=>rmSync(dir,{recursive:true,force:true}));
 const out=renderFixture(dir);
 const header={claim:{taskId:keyId(framesKey('n'))},direction:'n',sourcePresetHash};
 const timing={fps:out.fps,frameCount:out.frames.length};
 await assert.rejects(makeFrameBundle(dir,{...out,frameHashes:out.frames.map(()=>hash('f'))},timing,header,signal),/PRESET_2D_FRAME_HASH/);
 await assert.rejects(makeFrameBundle(dir,{...out,frames:out.frames.toReversed()},timing,header,signal),/PRESET_2D_FRAME_ORDER/);
 const {wire,manifestSha256}=await makeFrameBundle(dir,out,timing,header,signal);
 const {raw,frames,claim}=parseWire(wire);
 assert.equal(manifestSha256,finalHash('n',frames));
 assert.deepEqual(claim,header.claim);
 assert.equal(raw.length,frames[0].bytes*4);
 assert.equal(sha256(raw),sha256(Buffer.concat(Array(4).fill(realFixturePNG()))));
});
