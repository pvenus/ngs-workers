import test from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {chmodSync,existsSync,mkdtempSync,realpathSync,readFileSync,rmSync,statSync,writeFileSync,mkdirSync} from 'node:fs';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {crc32,deflateSync} from 'node:zlib';
import {
 claimPresetVideo,choosePresetVideo,clipKey,clipType,finalKey,finalType,imageType,imageKey,
 requestType,requestSchema,materializeRead,executePresetVideo,presetVideoPorts,
 DefinitePresetWriteRejection,presetPath,presetCapability,
} from './preset-video-worker.mjs';
import {directions,sha256,width,height,validateFrames,encodePresetVideo,validateProbe} from './preset-video-artifact.mjs';

const id='request.official.4ec9fcca84e0839dfb2b37142f360737b5b275ca71c56e53e06dc3e14ef35181';
const keyId=key=>'workflow.task.'+sha256(JSON.stringify([id,key]));
const sourcePresetId='walk',variantPresetId='walk-stylized-2d',sourcePresetHash='a'.repeat(64);
const hash=ch=>ch.repeat(64);
const packed=bytes=>({sha256:sha256(bytes),base64:bytes.toString('base64'),bytes:bytes.length});
const pngHeader=Buffer.from('89504e470d0a1a0a0000000d4948445200000300000001b00802000000000000000000','hex');
const mp4Header=Buffer.from('000000146674797069736f6d0000000069736f6d000000146d646174000000000000000000000000','hex');
const signal=new AbortController().signal;
const target=d=>({taskId:keyId(clipKey(d)),officialRequestId:id,type:clipType,key:clipKey(d)});
const finalize={taskId:keyId(finalKey),officialRequestId:id,type:finalType,key:finalKey};
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
 for(const d of directions)add(clipKey(d),clipType,'animation',{...common,direction:d},keyId(imageKey));
 assert.equal(keyId(clipKey('n')),'workflow.task.b4f406f6205c34981b5ae82076a34cee79bfb22433222f963676ea78a84d021f');
 add(finalKey,finalType,'animation',{...common,dependsOnTaskIds:directions.map(d=>keyId(clipKey(d)))},null);
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
const generated=Buffer.concat([mp4Header,Buffer.from('local fixture only')]);
function ports(d,{write}={}){
 return {read:async()=>d===null?{sourcePresetHash}:readClip(d),
  write:write??(async (claim,artifact)=>({ok:true,resultRef:d===null?
   'motion.2d.published.'+hash('c'):'motion.2d.clip.'+d+'.'+artifact.sha256}))};
}

test('one shared generated image gates eight specifically bound clip claims and exact finalizer',async t=>{
 const f=fixture(t);
 assert.equal(choosePresetVideo(f.file),undefined);
 assert.equal(claimPresetVideo(f.db,target('e')),null);
 f.image();
 assert.equal(choosePresetVideo(f.file).type,clipType);
 assert.equal(claimPresetVideo(f.db,finalize),null);
 const images=[],sources=[];
 for(const d of directions){
  const task=target(d);
  let scratch;
  const result=await executePresetVideo(f.db,task,signal,{pds:ports(d),
   native:async dir=>{
    scratch=dir;
    const job=JSON.parse(readFileSync(join(dir,'job.json')));
    assert.equal(job.frameCount,4);
    assert.equal(job.fps,24);
    assert.equal(job.referenceType,'portrait');
    assert.match(job.motionPrompt,/single-cycle directional/);
    assert(!job.motionPrompt.includes('걷기 2D'));
    images.push(sha256(readFileSync(join(dir,'portrait.png'))));
    sources.push(sha256(readFileSync(join(dir,'motion.mp4'))));
    return {frames:[]};
   },
   validate:async()=>{},encode:async()=>({mimeType:'video/mp4',...packed(generated)})});
  assert.equal(result.state,'completed',JSON.stringify(result));
  assert.equal(result.resultRef,'motion.2d.clip.'+d+'.'+sha256(generated));
  assert.equal(f.row(task.key).status,'completed');
  assert.equal(existsSync(scratch),false);
  assert.equal(claimPresetVideo(f.db,task),null);
 }
 assert.equal(new Set(images).size,1);
 assert.deepEqual(sources,directions.map(d=>readClip(d).reference.sha256));
 assert.equal(new Set(sources).size,8);
 assert.equal(choosePresetVideo(f.file).key,finalKey);
 let published=false;
 const out=await executePresetVideo(f.db,finalize,signal,{pds:ports(null,{write:async (claim,artifact)=>{
  assert.deepEqual(artifact,{});
  assert.deepEqual(Object.keys(claim).sort(),['claimStamp','officialRequestId','payloadSha256','projectId','taskId']);
  published=true;
  return {ok:true,resultRef:'motion.2d.published.'+hash('c')};
 }}),native:()=>assert.fail('finalization is not inference'),encode:()=>assert.fail('finalization is not encoding')});
 assert.equal(out.state,'completed');
 assert.equal(published,true);
 assert.equal(f.row(finalKey).resultRef,out.resultRef);
});

test('finalization does not accept missing, wrong, spoofed or misordered completed clip proof',t=>{
 const f=fixture(t);f.image();
 for(const d of directions)f.set(clipKey(d),'motion.2d.clip.'+d+'.'+hash('7'));
 f.set(clipKey('s'),'motion.2d.clip.n.'+hash('7'));
 assert.equal(claimPresetVideo(f.db,finalize),null);
 f.set(clipKey('s'),'motion.2d.clip.s.'+hash('7'));
 const wrong={...common,dependsOnTaskIds:directions.map(d=>keyId(clipKey(d))).reverse()};
 f.db.prepare('UPDATE workflow_tasks SET task_input=? WHERE task_key=?').run(canonical(wrong),finalKey);
 assert.equal(claimPresetVideo(f.db,finalize),null);
 assert.equal(f.row(finalKey).status,'pending');
});

test('rejects source drift, different image, modified timing and direction before renderer',t=>{
 const f=fixture(t);f.image();
 const bound=claimPresetVideo(f.db,target('w'));
 const read=readClip('w');
 assert.equal(materializeRead(bound,read).source.length,read.reference.bytes);
 assert.throws(()=>materializeRead(bound,{...read,sourcePresetHash:hash('1')}),/PRESET_2D_CLIP_REFERENCE_SEMANTICS/);
 assert.throws(()=>materializeRead(bound,{...read,direction:'e'}),/PRESET_2D_CLIP_SOURCE/);
 assert.throws(()=>materializeRead(bound,{...read,image:{...read.image,sha256:hash('3')}}),/PRESET_2D_CLIP_SHARED_IMAGE_BINDING/);
 assert.throws(()=>materializeRead(bound,{...read,reference:{...read.reference,fps:0}}),/PRESET_2D_CLIP_REFERENCE_SEMANTICS/);
 assert.throws(()=>materializeRead(bound,{...read,reference:{...read.reference,base64:packed(generated).base64}}),/PRESET_2D_ASSET_/);
});

test('ambiguous write reconciles without retry, definite scoped rejection remains retryable',async t=>{
 const f=fixture(t);f.image();
 const go=async (d,write)=>executePresetVideo(f.db,target(d),signal,{pds:ports(d,{write}),
  native:async()=>({}),validate:async()=>{},encode:async()=>({mimeType:'video/mp4',...packed(generated)})});
 assert.equal((await go('n',async()=>{throw Error('NETWORK');})).state,'requires_reconciliation');
 assert.equal(f.row(clipKey('n')).status,'running');
 assert.equal(claimPresetVideo(f.db,target('n')),null);
 assert.equal((await go('ne',async()=>{throw new DefinitePresetWriteRejection('MOTION_2D_REJECTED');})).state,'failed');
 assert.equal(f.row(clipKey('ne')).status,'failed');
 assert.equal((await go('e',async()=>({ok:true,resultRef:'motion.2d.clip.s.'+sha256(generated)}))).state,'requires_reconciliation');
});

test('PDS client requires exact capability and prewrite rejection shape',async()=>{
 let behavior={enabled:true,schemaVersion:presetCapability};
 const p=presetVideoPorts(async (path,options)=>{
  assert(path===presetPath||path===presetPath+'/read');
  assert(options.maxJsonBytes>0);
  if(options.onRejected)options.onRejected(behavior);
  return behavior;
 });
 assert(await p.ready(signal));
 behavior={enabled:true,schemaVersion:presetCapability,injected:true};
 assert.equal(await p.ready(signal),false);
 behavior={error:'MOTION_2D_REJECTED'};
 await assert.rejects(p.write({},{}),DefinitePresetWriteRejection);
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

test('lossless scratch frame hashes/order and real fixed FFmpeg/ffprobe yield one timed silent MP4',async t=>{
 if(!existsSync('/opt/homebrew/bin/ffmpeg')||!existsSync('/opt/homebrew/bin/ffprobe')){
  t.skip('the fixed Mac codec tools are not provisioned in this test environment');return;
 }
 const dir=realpathSync(mkdtempSync(join(tmpdir(),'preset-2d-encode-fixture-')));
 t.after(()=>rmSync(dir,{recursive:true,force:true}));
 const path=join(dir,'wan','frames');mkdirSync(path,{recursive:true});
 const png=realFixturePNG(),frames=['00000.png','00001.png','00002.png','00003.png'];
 for(const f of frames)writeFileSync(join(path,f),png);
 const fps=24,frameCount=frames.length,frameHashes=frames.map(()=>sha256(png));
 const out={width,height,fps,frames,frameHashes};
 await validateFrames(dir,out,{fps,frameCount});
 await assert.rejects(validateFrames(dir,{...out,frameHashes:frames.map(()=>hash('f'))},{fps,frameCount}),/PRESET_2D_FRAME_HASH/);
 await assert.rejects(validateFrames(dir,{...out,frames:frames.toReversed()},{fps,frameCount}),/PRESET_2D_FRAME_ORDER/);
 const a=await encodePresetVideo(dir,{fps,frameCount},signal);
 assert.equal(a.mimeType,'video/mp4');
 assert.equal(a.sha256,sha256(readFileSync(join(dir,'generic-2d.mp4'))));
 assert.equal(a.bytes,statSync(join(dir,'generic-2d.mp4')).size);
 assert.deepEqual([a.fps,a.frameCount,a.width,a.height],[fps,frameCount,width,height]);
 assert.throws(()=>validateProbe({streams:[]},{fps,frameCount}),/PRESET_2D_MP4_PROBE/);
});
