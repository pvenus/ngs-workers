import test from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {chmodSync,mkdtempSync,realpathSync,rmSync,existsSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {crc32,deflateSync} from 'node:zlib';

import {canonical} from './vendor/planner-f24d23a/envelope.mjs';
import {directions,sha256,width,height,probePresetEncoder,encodePresetVideo,validateProbe} from './preset-video-artifact.mjs';
import {choosePresetPostprocess,decodeFrameBundle,encodeKey,encodeType,executePresetPostprocess,framesKey,
 framesType,finalType,finalKey,motionTaskId,requestType,requestSchema,runPresetPostprocessFromPath} from './preset-video-postprocess.mjs';
import {presetPostprocessPorts,motionFramesSchema,motionPath} from './preset-video-ports.mjs';

const id='request.official.'+'c'.repeat(64),tid=key=>motionTaskId(id,key);
const common={sourcePresetId:'Walk.Ref',variantPresetId:'walk-flat'};
const target=d=>({taskId:tid(encodeKey(d)),officialRequestId:id,key:encodeKey(d),type:encodeType});
const finalize={taskId:tid(finalKey),officialRequestId:id,key:finalKey,type:finalType};
const finalRef='motion.2d.published.'+'a'.repeat(64);
const pngMagic=Buffer.from('89504e470d0a1a0a','hex');
function chunk(name,bytes){
 const kind=Buffer.from(name),size=Buffer.alloc(4),crc=Buffer.alloc(4);
 size.writeUInt32BE(bytes.length);crc.writeUInt32BE(crc32(Buffer.concat([kind,bytes])));
 return Buffer.concat([size,kind,bytes,crc]);
}
function png(shade){
 const h=Buffer.alloc(13);h.writeUInt32BE(width);h.writeUInt32BE(height,4);h[8]=8;h[9]=2;
 const pixels=Buffer.alloc((1+width*3)*height,shade);
 for(let y=0;y<height;y++)pixels[y*(1+width*3)]=0;
 return Buffer.concat([pngMagic,chunk('IHDR',h),chunk('IDAT',deflateSync(pixels)),chunk('IEND',Buffer.alloc(0))]);
}
const pngs=[png(105),png(122)];
function bundle(d){
 const manifest={direction:d,sourcePresetHash:'f'.repeat(64),width,height,
  frames:pngs.map(bytes=>({bytes:bytes.length,sha256:sha256(bytes)}))};
 const header=Buffer.from(JSON.stringify({...manifest,fps:24}));
 const count=Buffer.alloc(4);count.writeUInt32BE(header.length);
 return {bytes:Buffer.concat([count,header,...pngs]),hash:sha256(canonical(manifest))};
}
function fixture(t){
 const root=realpathSync(mkdtempSync(join(tmpdir(),'postprocess-motion-fixture-')));
 const path=join(root,'queue.sqlite'),db=new DatabaseSync(path);
 chmodSync(path,0o600); t.after(()=>{db.close();rmSync(root,{recursive:true,force:true});});
 db.exec(`CREATE TABLE workflow_runs(official_request_id TEXT PRIMARY KEY,project_id TEXT,
 request_type TEXT,schema_version TEXT,payload TEXT,payload_sha256 TEXT,state TEXT,reason TEXT,registered_at INTEGER);
 CREATE TABLE workflow_tasks(task_id TEXT PRIMARY KEY,official_request_id TEXT,worker_type TEXT,task_type TEXT,
 task_key TEXT,task_input TEXT,status TEXT,result_ref TEXT,depends_on_task_id TEXT,updated_at INTEGER,retry_count INTEGER)`);
 const p=canonical({schemaVersion:requestSchema,projectId:'binari',...common,label:'걷기 2D'});
 db.prepare("INSERT INTO workflow_runs VALUES (?,?,?,?,?,?,'planned',NULL,1)").run(id,'binari',requestType,requestSchema,p,sha256(p));
 const add=(key,w,type,input,dependency)=>db.prepare("INSERT INTO workflow_tasks VALUES(?,?,?,?,?,?,'pending',NULL,?,1,0)")
  .run(tid(key),id,w,type,key,canonical(input),dependency);
 add('motion_2d_image','image',requestType+'.image',common,null);
 for(const d of directions){
  add(framesKey(d),'animation',framesType,{...common,direction:d},tid('motion_2d_image'));
  add(encodeKey(d),'postprocess',encodeType,{...common,direction:d},tid(framesKey(d)));
 }
 add(finalKey,'postprocess',finalType,{...common,dependsOnTaskIds:directions.map(d=>tid(encodeKey(d)))},null);
 const result=(key,ref)=>db.prepare("UPDATE workflow_tasks SET status='completed',result_ref=? WHERE task_key=?").run(ref,key);
 const frame=d=>result(framesKey(d),'motion.2d.frames.'+d+'.'+bundle(d).hash);
 const row=key=>db.prepare('SELECT * FROM workflow_tasks WHERE task_key=?').get(key);
 return {db,path,result,frame,row};
}

test('MP4 duration allows sub-millisecond timestamp rounding, not a lost frame or changed fps',()=>{
 const timing={fps:24,frameCount:41};
 const stream={codec_type:'video',codec_name:'h264',width,height,pix_fmt:'yuv420p',
  r_frame_rate:'24/1',avg_frame_rate:'24/1',nb_read_frames:'41',duration:'1.708008'};
 const proof=changes=>({streams:[{...stream,...changes}],format:{format_name:'mov,mp4'}});
 assert.doesNotThrow(()=>validateProbe(proof({}),timing));
 assert.throws(()=>validateProbe(proof({duration:'1.707'}),timing),/PRESET_2D_MP4_PROBE/);
 assert.throws(()=>validateProbe(proof({nb_read_frames:'40'}),timing),/PRESET_2D_MP4_PROBE/);
 assert.throws(()=>validateProbe(proof({avg_frame_rate:'25/1'}),timing),/PRESET_2D_MP4_PROBE/);
});

test('validated binary frames, actual CPU encode/probe, one directional proof and cleanup',async t=>{
 if(!await probePresetEncoder()){t.skip('fixed Homebrew CPU encoder not present');return;}
 const f=fixture(t);f.frame('n');
 assert.deepEqual(Object.assign({},choosePresetPostprocess(f.path)),target('n'));
 let scratch;
 const pds={readFrames:async claim=>{assert.equal(claim.claimStamp,f.row(encodeKey('n')).updated_at);return bundle('n').bytes;},
  write:async(_claim,artifact)=>{
   assert.deepEqual(Object.keys(artifact).sort(),['base64','sha256']);
   assert.equal(sha256(Buffer.from(artifact.base64,'base64')),artifact.sha256);
   return {ok:true,resultRef:'motion.2d.clip.n.'+artifact.sha256};
 }};
 const out=await runPresetPostprocessFromPath({dbPath:f.path,...target('n'),pds,encode:async(dir,...rest)=>{
  scratch=dir;return encodePresetVideo(dir,...rest);
 }});
 assert.equal(out.state,'completed');
 assert.equal(f.row(encodeKey('n')).result_ref,out.resultRef);
 assert.equal(existsSync(scratch),false);
});

test('finalization requires eight exact ordered directional proofs plus exact derived children',async t=>{
 const f=fixture(t);
 for(const d of directions){
  f.frame(d);if(d!=='w')f.result(encodeKey(d),'motion.2d.clip.'+d+'.'+'b'.repeat(64));
 }
 let writes=0;
 const pds={write:async(_claim,a)=>{writes++;assert.deepEqual(a,{});return {ok:true,resultRef:finalRef};}};
 assert.equal((await executePresetPostprocess(f.db,finalize,undefined,{pds})).state,'not_claimable');
 f.result(encodeKey('w'),'motion.2d.clip.n.'+'b'.repeat(64));
 assert.equal((await executePresetPostprocess(f.db,finalize,undefined,{pds})).state,'not_claimable');
 f.result(encodeKey('w'),'motion.2d.clip.w.'+'b'.repeat(64));
 assert.deepEqual(Object.assign({},choosePresetPostprocess(f.path)),finalize);
 assert.equal((await executePresetPostprocess(f.db,finalize,undefined,{pds})).resultRef,finalRef);
 assert.equal(writes,1);
});

test('bundle parser rejects modified bytes, bad order, tampered hash and trailing bytes',()=>{
 const b=bundle('sw'),input={d:'sw',proof:b.hash};
 assert.equal(decodeFrameBundle(b.bytes,input).timing.fps,24);
 // Fixed exact framing; ensure both a single corrupt byte and trailing payload fail.
 const damaged=Buffer.from(b.bytes);damaged[damaged.length-2]^=1;
 assert.throws(()=>decodeFrameBundle(damaged,input),/FRAME_HASH/);
 assert.throws(()=>decodeFrameBundle(Buffer.concat([b.bytes,Buffer.alloc(1)]),input),/FRAME_LENGTH/);
 assert.throws(()=>decodeFrameBundle(b.bytes,{...input,proof:'0'.repeat(64)}),/FRAME_PROOF/);
 assert.throws(()=>decodeFrameBundle(b.bytes,{...input,d:'se'}),/FRAME_MANIFEST/);
});

test('transport exact binary routes and fixed publication schema',async()=>{
 const calls=[];
 const pds=presetPostprocessPorts({transport:async(path,opts)=>{
  calls.push([path,opts]);return path.endsWith('/read')?bundle('n').bytes:
   Buffer.from(JSON.stringify(path.endsWith('/frames')?{enabled:true,schemaVersion:motionFramesSchema}:{ok:true,resultRef:finalRef}));
 }});
 const claim={taskId:'fixture'};
 assert.equal(await pds.ready(),true);
 assert.deepEqual(await pds.readFrames(claim),bundle('n').bytes);
 await pds.write(claim,{});
 assert.deepEqual(calls.map(([path,o])=>[path,o.type,o.body]),[
  [motionPath+'/frames','application/json',undefined],[motionPath+'/frames/read','application/octet-stream',{claim}],
  [motionPath,'application/json',{claim,artifact:{}}]]);
});

test('pre-publication refusal fails; lost final acknowledgement still requires reconciliation',async t=>{
 const f=fixture(t);f.frame('n');
 const refused=await executePresetPostprocess(f.db,target('n'),undefined,{pds:{
  readFrames:async()=>Buffer.alloc(1),write:async()=>assert.fail('no publication')}});
 assert.equal(refused.state,'failed');
 for(const d of directions){
  f.frame(d);f.result(encodeKey(d),'motion.2d.clip.'+d+'.'+'b'.repeat(64));
 }
 const uncertain=await executePresetPostprocess(f.db,finalize,undefined,{pds:{write:async()=>{throw Error('socket lost');}}});
 assert.equal(uncertain.state,'requires_reconciliation');
 assert.equal(f.row(finalKey).status,'running');
});
