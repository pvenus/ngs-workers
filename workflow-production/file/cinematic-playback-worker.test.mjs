import assert from 'node:assert/strict';
import test from 'node:test';
import {createServer} from 'node:http';
import {DatabaseSync} from 'node:sqlite';
import {mkdtempSync,realpathSync,rmSync,writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {canonical,sha} from './vendor/planner-f24d23a/envelope.mjs';
import {executeOnce,parseArgs,pollOnce} from './runner.mjs';
import {playbackKey,playbackSchema,playbackType} from './cinematic-playback-worker.mjs';
import {cinematicPlaybackCapability,cinematicPlaybackPath,localPorts,requestLocalJson} from './local-ports.mjs';

const officialRequestId='request.official.'+'4'.repeat(64);
const sequenceId='request.official.'+'3'.repeat(64);
const taskId='workflow.task.'+sha(canonical([officialRequestId,playbackKey]));
const resultRef='binari/cinematics/sequences/'+sequenceId+'/timeline.json';
const playback={durationFrames:80,speedCurve:[{frame:0,rate:1},{frame:79,rate:0.2}],
 cameraCurve:[{frame:0,zoom:1.1,x:0.37,y:0.59},{frame:38,zoom:2.19,x:1,y:0}]};
const input={sequenceId,expectedRevision:0,playback};
const payload=canonical({schemaVersion:playbackSchema,projectId:'binari',...input});
function setup(t,override){
 const dir=realpathSync(mkdtempSync(join(tmpdir(),'cinematic-playback-stage-')));
 const dbPath=join(dir,'db.sqlite');writeFileSync(dbPath,'',{mode:0o600});
 const db=new DatabaseSync(dbPath);
 t.after(()=>{db.close();rmSync(dir,{recursive:true,force:true});});
 db.exec(`CREATE TABLE workflow_runs(official_request_id,project_id,request_type,schema_version,payload,payload_sha256,registered_at,state,updated_at,reason);
  CREATE TABLE workflow_tasks(task_id,official_request_id,task_key,task_type,worker_type,task_input,result_ref,updated_at,depends_on_task_id,status,retry_count);`);
 const principal=override?canonical({schemaVersion:playbackSchema,projectId:'binari',...override}):payload;
 db.prepare("INSERT INTO workflow_runs VALUES(?,?,?,?,?,?,1,'planned',1,NULL)").run(officialRequestId,'binari',playbackType,playbackSchema,principal,sha(principal));
 db.prepare("INSERT INTO workflow_tasks VALUES(?,?,?,?,?,?,NULL,2,NULL,'pending',0)").run(taskId,officialRequestId,playbackKey,
  playbackType,'file',canonical(override??input));
 const state=()=>db.prepare('SELECT status,result_ref,updated_at,retry_count FROM workflow_tasks WHERE task_id=?').get(taskId);
 return {db,dbPath,state,officialRequestId,taskId};
}
const cap={enabled:true,schemaVersion:cinematicPlaybackCapability};
async function withHttp(t,drive){
 const server=createServer(async(req,res)=>{
  const chunks=[];for await(const chunk of req)chunks.push(chunk);
  const packet=chunks.length?JSON.parse(Buffer.concat(chunks)):null;
  const {status,body,destroy}=await drive(req,packet);
  if(destroy){res.destroy();return;}
  res.writeHead(status??200,{'Content-Type':'application/json'}).end(JSON.stringify(body));
 });
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 t.after(()=>new Promise(resolve=>server.close(resolve)));
 return localPorts({transport:o=>{assert.equal(o.port,8030);return requestLocalJson({...o,port:server.address().port});}});
}
test('metadata-only resident uses exact claim and endpoint with no PNG or encoder work',async t=>{
 const f=setup(t),calls=[];
 const pds=await withHttp(t,async(req,packet)=>{
  assert.equal(req.url,cinematicPlaybackPath);
  calls.push(req.method);
  if(req.method==='GET')return {body:cap};
  assert.equal(f.state().status,'running');
  assert.deepEqual(packet,{claim:{taskId,officialRequestId,projectId:'binari',payloadSha256:sha(payload),claimStamp:f.state().updated_at}});
  return {body:{ok:true,resultRef}};
 });
 assert.equal((await pollOnce({...f,pds})).state,'completed');
 assert.equal(f.state().status,'completed');assert.equal(f.state().result_ref,resultRef);
 assert.equal((await executeOnce({...f,pds,operation:playbackType})).state,'not_claimable');
 assert.deepEqual(calls,['GET','POST','GET']);
 assert.equal(parseArgs(['--execute','--db',f.dbPath,'--official-request-id',officialRequestId,'--task-id',taskId,'--operation',playbackType]).operation,playbackType);
});
test('revision conflict is terminal, consumes no upload and never overwrites a newer edit',async t=>{
 const f=setup(t);let posts=0;
 const pds=await withHttp(t,async(req,packet)=>{
  if(!packet)return {body:cap};
  posts++;return {status:409,body:{error:'WORKFLOW_CINEMATIC_PLAYBACK_CONFLICT'}};
 });
 assert.equal((await pollOnce({...f,pds})).code,'CINEMATIC_PLAYBACK_REVISION_CONFLICT');
 assert.equal(f.state().status,'failed');assert.equal(f.state().retry_count,3);assert.equal(f.state().result_ref,null);
 f.db.prepare('UPDATE workflow_tasks SET updated_at=1').run();
 assert.equal((await pollOnce({...f,pds})).state,'idle');assert.equal(posts,1);
});
test('same-claim retry can acknowledge already-written metadata; unknown timeout stays fenced',async t=>{
 for(const works of [true,false]){
  const f=setup(t),posts=[];
  const pds=await withHttp(t,async(_req,packet)=>{
   if(!packet)return {body:cap};
   posts.push(packet);
   if(!works||posts.length===1)return {destroy:true};
   return {body:{ok:true,resultRef}};
  });
  const result=await pollOnce({...f,pds});
  assert.equal(result.state,works?'completed':'requires_reconciliation');
  assert.equal(f.state().status,works?'completed':'running');
  assert.equal(posts.length,2);assert.deepEqual(posts[0],posts[1]);
  assert.equal((await pollOnce({...f,pds})).state,'idle');
 }
});
test('bad expected revision, source ID, unbounded camera or stale claim never post or claim completion',async t=>{
 for(const bad of [{...input,expectedRevision:-1},{...input,sequenceId:sequenceId+'\n'},{...input,sequenceId:officialRequestId},
  {...input,playback:{...playback,cameraCurve:[{frame:0,zoom:4,x:0.5,y:0.5}]}}]){
  const f=setup(t,bad);
  const pds={cinematicPlaybackReady:async()=>true,updateCinematicPlayback:async()=>assert.fail('must not POST malformed request')};
  assert.equal((await pollOnce({...f,pds})).state,'blocked');assert.equal(f.state().status,'pending');
 }
 const f=setup(t);
 const pds={cinematicPlaybackReady:async()=>true,updateCinematicPlayback:async()=>{
  f.db.prepare("UPDATE workflow_runs SET reason='withdrawn'").run();
  return {ok:true,resultRef};
 }};
 assert.equal((await pollOnce({...f,pds})).state,'requires_reconciliation');assert.equal(f.state().result_ref,null);
});
