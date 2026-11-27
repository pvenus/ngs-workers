import {test} from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {createHash} from 'node:crypto';
import {mkdtempSync,realpathSync,chmodSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {mapCanonical as canonical} from './map-contract.mjs';
import {assemblyStage,createAssemblyRenderer} from './assembly-worker.mjs';
import {documentTick,executeOnce,parseArgs} from './runner.mjs';
import {localPorts} from './local-ports.mjs';

const hash=x=>createHash('sha256').update(x).digest('hex');
const id=x=>'request.official.'+x.repeat(64);
const officialRequestId=id('a'),mapId=id('b'),existingId=id('c');
const taskId='workflow.task.'+hash(canonical([officialRequestId,assemblyStage]));
const noModel=()=>assert.fail('Assembly must not initialize a model renderer');
function fixture(t,operation,{wrongReceipt=false,wrongMap=false,replay=false,invalidModel=false,batch=false,scale,modelScale}={}){
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
  ...(scale?{scale}:{}),bindings:Object.fromEntries(['table','bowl','well','lid'].map(k=>[k,{propId:k,revision:1,sha256:'a'.repeat(64)}]))},puzzles=batch?[puzzle,{...puzzle,id:'other-ritual'}]:[puzzle];
 const payload=generate?{schemaVersion:batch?'ngs.assembly-generate.v2':'ngs.assembly-generate.v1',projectId:'binari',title:'조립',mapId,intent:'의식을 준비한다.',...(batch?{puzzles:puzzles.map(({id,type},i)=>({id,type,intent:'퍼즐 '+i}))}:{puzzleType:puzzle.type})}:
   {schemaVersion:'ngs.assembly-update.v1',projectId:'binari',assemblyId,expectedRevision:2,puzzles};
 const wire=canonical(payload),digest=hash(wire);
 db.prepare("INSERT INTO workflow_runs VALUES(?,'planned',NULL,'binari',?,?,?,?,1)").run(officialRequestId,operation,payload.schemaVersion,wire,digest);
 db.prepare("INSERT INTO workflow_tasks VALUES(?,?,?,?,?,'document',NULL,NULL,'pending',1,0)").run(taskId,officialRequestId,wire,operation,assemblyStage);
 const context={startRegionId:'north',regions:['north','south'].map(id=>({id,name:id,description:'길',
  backgroundId:'background.'+'a'.repeat(64),imageSha256:'b'.repeat(64)})),connections:[]};
 const generated={layout:{...puzzle.layout,altar:{...puzzle.layout.altar,x:42}},attachments:puzzle.attachments};
 const batchOutput=puzzles.map(({id},i)=>({id,...generated,layout:{...generated.layout,altar:{...generated.layout.altar,x:42+i}}}));
 const inputs=[];
 const render=async input=>{inputs.push(input);
  if(batch)return {puzzles:invalidModel?[batchOutput[0],batchOutput[0]]:[...batchOutput].reverse()};
  return modelScale?{...generated,scale:modelScale}:invalidModel?{...generated,bindings:{}}:generated;};
 const resultRef=`binari/cinematics/assemblies/${assemblyId}/${revision}.json`;
 let prepared,activeClaim;
 const calls=[],pds=localPorts({async transport({method,path,body,port}){
  assert.equal(port,8030);calls.push(path);
  if(method==='GET')return {enabled:true,schemaVersion:'workflow.assembly.v1'};
  assert.equal(row().status,'running');
  assert.deepEqual(body.claim,{taskId,officialRequestId,projectId:'binari',claimStamp:row().updated_at,payloadSha256:digest});
  if(path.endsWith('/read')){
   activeClaim=body.claim;
   prepared={schemaVersion:'ngs.assembly.v1',projectId:'binari',assemblyId,revision,updatedAt:body.claim.claimStamp-(replay?1:0),
    officialRequestId,title:'조립',map:{mapId:wrongMap?id('d'):mapId,revision:5,officialRequestId:id('e')},puzzles};
   return generate?{state:prepared,context}:{state:prepared};
  }
  assert.deepEqual(body,{claim:activeClaim,state:generate&&!replay?{...prepared,puzzles:batch?puzzles.map((row,i)=>({...row,...batchOutput[i]})):[{...puzzle,...generated}]}:prepared});
  return {ok:true,resultRef:wrongReceipt?'wrong':resultRef};
 }});
 const row=()=>db.prepare('SELECT status,result_ref,retry_count,updated_at FROM workflow_tasks').get();
 return {dbPath,pds,operation,taskId,officialRequestId,row,resultRef,calls,inputs,render,rendererFor:generate?()=>render:noModel};
}
test('resident assembly.generate routes exact claimed read and publish and sends only claimed map, bindings and intent to the renderer',async t=>{
 const f=fixture(t,'assembly.generate');
 assert.equal(parseArgs(['--run','--db',f.dbPath,'--operation',f.operation]).operation,f.operation);
 assert.deepEqual(await documentTick(f),{state:'completed',taskId,resultRef:f.resultRef,operation:f.operation});
 assert.equal(f.row().status,'completed');
 assert.deepEqual(Object.keys(f.inputs[0]),['title','intent','map','bindings']);
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
 assert.deepEqual(await executeOnce(f),{state:'failed',taskId,code,stage:flag==='wrongMap'?'prepare':'publish'});
 assert.equal(f.row().status,'failed');
 assert.equal(f.row().retry_count,0);
 assert.equal(f.row().result_ref,null);
 if(flag==='wrongMap')assert.equal(f.calls.length,2);
});

test('committed generation retry republishes immutable state without running CLI a second time',async t=>{
 const f=fixture(t,'assembly.generate',{replay:true});
 assert.equal((await documentTick(f)).state,'completed');
 assert.deepEqual(f.inputs,[]);
});
test('malformed model result becomes normal failure without publishing defaults',async t=>{
 const f=fixture(t,'assembly.generate',{invalidModel:true});
 assert.deepEqual(await documentTick(f),{state:'failed',taskId,code:'ASSEMBLY_CLI_OUTPUT_INVALID',stage:'cli',operation:f.operation});
 assert.equal(f.calls.length,2);
});
test('CLI uses the shared typed Codex executor and placement-only output schema',async ()=>{
 const sample={title:'조립',intent:'테스트',map:{},bindings:{}};
 const render=createAssemblyRenderer({codexPath:process.execPath,execute:async ({prompt,outputSchema})=>{
  assert.ok(prompt.endsWith(JSON.stringify(sample)));
  assert.deepEqual(outputSchema.required,['layout','attachments']);
  assert.equal(outputSchema.additionalProperties,false);
  return {layout:{},attachments:{}};
 }});
 assert.deepEqual(await render(sample),{layout:{},attachments:{}});
});

test('v2 groups two independently annotated puzzles in one CLI call and merges placement by exact ID',async t=>{
 const f=fixture(t,'assembly.generate',{batch:true});
 assert.equal((await documentTick(f)).state,'completed');
 assert.equal(f.inputs.length,1);
 assert.equal(f.inputs[0].intent,'의식을 준비한다.');
 assert.deepEqual(f.inputs[0].puzzles.map(({id,intent})=>[id,intent]),[['ritual-well-water','퍼즐 0'],['other-ritual','퍼즐 1']]);
});
test('v2 refuses duplicate model IDs rather than publishing a partial batch',async t=>{
 const f=fixture(t,'assembly.generate',{batch:true,invalidModel:true});
 assert.deepEqual(await documentTick(f),{state:'failed',taskId,code:'ASSEMBLY_CLI_OUTPUT_INVALID',stage:'cli',operation:f.operation});
 assert.equal(f.calls.length,2);
});
test('v2 typed executor uses exact puzzle-ID geometry rows and one bounded list',async ()=>{
 const sample={title:'조립',intent:'공통',map:{},puzzles:[{id:'first',intent:'개별'}]};
 const render=createAssemblyRenderer({codexPath:process.execPath,execute:async ({prompt,outputSchema})=>{
  assert.ok(prompt.endsWith(JSON.stringify(sample)));
  assert.match(prompt,/each puzzle’s own intent/);
  assert.deepEqual(outputSchema.required,['puzzles']);
  const rows=outputSchema.properties.puzzles;
  assert.equal(rows.maxItems,8);
  assert.deepEqual(rows.items.required,['id','layout','attachments']);
  assert.equal(rows.items.additionalProperties,false);
  return {puzzles:[]};
 }});
 assert.deepEqual(await render(sample),{puzzles:[]});
});

const resized={table:10,bowl:125,well:300,lid:99};
test('manual assembly update publishes exact clamped-range scales with no model call',async t=>{
 const f=fixture(t,'assembly.update',{scale:resized});
 assert.equal((await executeOnce(f)).state,'completed');
 assert.deepEqual(f.inputs,[]);
});
test('generation rejects a scale proposed outside the CLI placement contract',async t=>{
 const f=fixture(t,'assembly.generate',{modelScale:resized});
 assert.deepEqual(await documentTick(f),{state:'failed',taskId,code:'ASSEMBLY_CLI_OUTPUT_INVALID',stage:'cli',operation:f.operation});
 assert.equal(f.calls.length,2);
});
