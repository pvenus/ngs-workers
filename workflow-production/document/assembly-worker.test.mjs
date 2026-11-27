import {test} from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {createHash} from 'node:crypto';
import {mkdtempSync,realpathSync,chmodSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {mapCanonical as canonical} from './map-contract.mjs';
import {assemblyStage} from './assembly-worker.mjs';
import {documentTick,executeOnce,parseArgs} from './runner.mjs';
import {localPorts} from './local-ports.mjs';

const hash=x=>createHash('sha256').update(x).digest('hex');
const id=x=>'request.official.'+x.repeat(64);
const officialRequestId=id('a'),mapId=id('b'),existingId=id('c');
const taskId='workflow.task.'+hash(canonical([officialRequestId,assemblyStage]));
const noModel=()=>assert.fail('Assembly must not initialize a model renderer');
function fixture(t,operation,{wrongReceipt=false,wrongMap=false}={}){
 const dir=realpathSync(mkdtempSync(join(tmpdir(),'assembly-worker-test-'))),dbPath=join(dir,'planner.sqlite');
 const db=new DatabaseSync(dbPath);chmodSync(dbPath,0o600);
 t.after(()=>{db.close();rmSync(dir,{force:true,recursive:true});});
 db.exec(`CREATE TABLE workflow_runs(official_request_id TEXT PRIMARY KEY,state TEXT,reason TEXT,project_id TEXT,
 request_type TEXT,schema_version TEXT,payload TEXT,payload_sha256 TEXT,registered_at INTEGER);
 CREATE TABLE workflow_tasks(task_id TEXT PRIMARY KEY,official_request_id TEXT,task_input TEXT,task_type TEXT,
 task_key TEXT,worker_type TEXT,depends_on_task_id TEXT,result_ref TEXT,status TEXT,updated_at INTEGER,retry_count INTEGER)`);
 const generate=operation==='assembly.generate',assemblyId=generate?officialRequestId:existingId,revision=generate?1:3;
 const puzzle={id:'ritual-well-water',type:'binari.ritual-well-water-demo',
  layout:{altar:{regionId:'north',x:40,y:50},well:{regionId:'south',x:60,y:50}},
  attachments:{bowl:{x:0,y:1},lid:{x:2,y:3}},
  bindings:Object.fromEntries(['table','bowl','well','lid'].map(k=>[k,{propId:k,revision:1,sha256:'a'.repeat(64)}]))},puzzles=[puzzle];
 const payload=generate?{schemaVersion:'ngs.assembly-generate.v1',projectId:'binari',title:'조립',mapId,puzzleType:puzzle.type,intent:'의식을 준비한다.'}:
   {schemaVersion:'ngs.assembly-update.v1',projectId:'binari',assemblyId,expectedRevision:2,puzzles};
 const wire=canonical(payload),digest=hash(wire);
 db.prepare("INSERT INTO workflow_runs VALUES(?,'planned',NULL,'binari',?,?,?,?,1)").run(officialRequestId,operation,payload.schemaVersion,wire,digest);
 db.prepare("INSERT INTO workflow_tasks VALUES(?,?,?,?,?,'document',NULL,NULL,'pending',1,0)").run(taskId,officialRequestId,wire,operation,assemblyStage);
 const resultRef=`binari/cinematics/assemblies/${assemblyId}/${revision}.json`;
 let prepared,activeClaim;
 const calls=[],pds=localPorts({async transport({method,path,body,port}){
  assert.equal(port,8030);calls.push(path);
  if(method==='GET')return {enabled:true,schemaVersion:'workflow.assembly.v1'};
  assert.equal(row().status,'running');
  assert.deepEqual(body.claim,{taskId,officialRequestId,projectId:'binari',claimStamp:row().updated_at,payloadSha256:digest});
  if(path.endsWith('/read')){
   activeClaim=body.claim;
   prepared={schemaVersion:'ngs.assembly.v1',projectId:'binari',assemblyId,revision,updatedAt:body.claim.claimStamp,
    officialRequestId,title:'조립',map:{mapId:wrongMap?id('d'):mapId,revision:5,officialRequestId:id('e')},puzzles};
   return {state:prepared};
  }
  assert.deepEqual(body,{claim:activeClaim,state:prepared});
  return {ok:true,resultRef:wrongReceipt?'wrong':resultRef};
 }});
 const row=()=>db.prepare('SELECT status,result_ref,retry_count,updated_at FROM workflow_tasks').get();
 return {dbPath,pds,operation,taskId,officialRequestId,row,resultRef,calls,rendererFor:noModel};
}
test('resident assembly.generate routes exact claimed read and publish without constructing a model renderer',async t=>{
 const f=fixture(t,'assembly.generate');
 assert.equal(parseArgs(['--run','--db',f.dbPath,'--operation',f.operation]).operation,f.operation);
 assert.deepEqual(await documentTick(f),{state:'completed',taskId,resultRef:f.resultRef,operation:f.operation});
 assert.equal(f.row().status,'completed');
 assert.deepEqual(f.calls, ['/v1/assemblies/ownership/workflow-assembly','/v1/assemblies/ownership/workflow-assembly/read','/v1/assemblies/ownership/workflow-assembly']);
 assert.equal((await documentTick(f)).state,'idle');
});
test('explicit assembly.update publishes the prepared next revision with edits preserved',async t=>{
 const f=fixture(t,'assembly.update');
 assert.deepEqual(await executeOnce(f),{state:'completed',taskId,resultRef:f.resultRef});
 assert.equal(f.row().result_ref,f.resultRef);
});
for(const [flag,code] of [['wrongReceipt','ASSEMBLY_SAVE_NOT_CONFIRMED'],['wrongMap','ASSEMBLY_PREPARE_INVALID']])test(flag,async t=>{
 const f=fixture(t,'assembly.generate',{[flag]:true});
 assert.deepEqual(await executeOnce(f),{state:'failed',taskId,code});
 assert.equal(f.row().status,'failed');
 assert.equal(f.row().retry_count,0);
 assert.equal(f.row().result_ref,null);
 if(flag==='wrongMap')assert.equal(f.calls.length,2);
});
