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
function fixture(t,operation,{wrongReceipt=false,wrongMap=false,replay=false,invalidModel=false,batch=false,mixed=false,wrongPin=false,overridePin=false,unreachable=false,defined=false,rejectDefinition=false,omitValidator=false,scale,modelScale,portalConditions}={}){
 const dir=realpathSync(mkdtempSync(join(tmpdir(),'assembly-worker-test-'))),dbPath=join(dir,'planner.sqlite');
 const db=new DatabaseSync(dbPath);chmodSync(dbPath,0o600);
 t.after(()=>{db.close();rmSync(dir,{force:true,recursive:true});});
 db.exec(`CREATE TABLE workflow_runs(official_request_id TEXT PRIMARY KEY,state TEXT,reason TEXT,project_id TEXT,
 request_type TEXT,schema_version TEXT,payload TEXT,payload_sha256 TEXT,registered_at INTEGER);
 CREATE TABLE workflow_tasks(task_id TEXT PRIMARY KEY,official_request_id TEXT,task_input TEXT,task_type TEXT,
 task_key TEXT,worker_type TEXT,depends_on_task_id TEXT,result_ref TEXT,status TEXT,updated_at INTEGER,retry_count INTEGER)`);
 if(mixed||defined)batch=true;
 const generate=operation==='assembly.generate',assemblyId=generate?officialRequestId:existingId,revision=generate?1:3;
 const puzzle={id:'ritual-well-water',type:'binari.ritual-well-water-demo',
  layout:{altar:{regionId:'north',x:40,y:50},well:{regionId:'south',x:60,y:50}},
  attachments:{bowl:{x:0,y:1},lid:{x:2,y:3}},
  ...(scale?{scale}:{}),...(portalConditions?{portalConditions}:{}),bindings:Object.fromEntries(['table','bowl','well','lid'].map(k=>[k,{propId:k,revision:1,sha256:'a'.repeat(64)}]))};
 const prop={propId:'spirit-mask',revision:3,sha256:'c'.repeat(64)};
 const spirit={id:'reveal-spirit',type:'binari.ritual-spirit-manifestation',layout:{spirit:{regionId:'north',x:50,y:50}},
  attachments:{},bindings:{spirit:wrongPin?{...prop,revision:4}:prop},scale:{spirit:100}};
 const puzzles=defined?[puzzle]:mixed?[puzzle,spirit]:batch?[puzzle,{...puzzle,id:'other-ritual'}]:[puzzle];
 const payload=generate?{schemaVersion:defined?'ngs.assembly-generate.v4':mixed?'ngs.assembly-generate.v3':batch?'ngs.assembly-generate.v2':'ngs.assembly-generate.v1',projectId:'binari',title:'조립',mapId,intent:'의식을 준비한다.',...(batch?{puzzles:puzzles.map(({id,type},i)=>({id,type,intent:'퍼즐 '+i,...(id===spirit.id?{prop}:{})}))}:{puzzleType:puzzle.type})}:
   {schemaVersion:'ngs.assembly-update.v1',projectId:'binari',assemblyId,expectedRevision:2,puzzles};
 const wire=canonical(payload),digest=hash(wire);
 db.prepare("INSERT INTO workflow_runs VALUES(?,'planned',NULL,'binari',?,?,?,?,1)").run(officialRequestId,operation,payload.schemaVersion,wire,digest);
 db.prepare("INSERT INTO workflow_tasks VALUES(?,?,?,?,?,'document',NULL,NULL,'pending',1,0)").run(taskId,officialRequestId,wire,operation,assemblyStage);
 const context={startRegionId:'north',regions:['north','south'].map(id=>({id,name:id,description:'길',
  backgroundId:'background.'+'a'.repeat(64),imageSha256:'b'.repeat(64)})),connections:[]};
 const generated={layout:{...puzzle.layout,altar:{...puzzle.layout.altar,x:42}},attachments:puzzle.attachments};
 const ref=(role,state)=>({role,state}),action=(kind,target,item=null)=>({kind,target,item});
 const rule=(module,action,from,to,requires=[])=>({module,action,from,to,requires});
 const rules=[
  rule('inventory.move.v1',action('pickup','bowl'),ref('bowl','altar-empty'),ref('bowl','carried-empty')),
  rule('switch.v1',action('activate','lid'),ref('lid','closed'),ref('lid','open')),
  rule('prop.interact.v1',action('drop','well','bowl'),ref('bowl','carried-empty'),ref('bowl','carried-filled'),[ref('lid','open')]),
  rule('inventory.move.v1',action('drop','altar','bowl'),ref('bowl','carried-filled'),ref('bowl','altar-filled')),
 ];
 const definition={schemaVersion:'ngs.puzzle-definition.v1',
  entities:Object.entries({bowl:['altar-empty','carried-empty','carried-filled','altar-filled'],well:['ready'],altar:['ready'],lid:['closed','open']})
   .map(([role,ids])=>({role,initial:ids[0],states:ids.map(id=>({id,held:id.startsWith('carried-'),payload:role==='bowl'?(id.endsWith('-filled')?'filled':'empty'):id}))})),
  rules,goal:ref('bowl','altar-filled'),solution:rules.map(row=>row.action)};
 const validations=[],validateDefinition=omitValidator?undefined:async data=>{validations.push(data);assert.deepEqual(data,definition);return !rejectDefinition;};
 const batchOutput=puzzles.map(({id},i)=>id===spirit.id?
  {id,layout:{spirit:{regionId:unreachable?'south':'north',x:16,y:88}},scale:{spirit:160},...(overridePin?{bindings:{spirit:{...prop,revision:9}}}:{})}:
  {id,...generated,layout:{...generated.layout,altar:{...generated.layout.altar,x:42+i}},...(defined?{definition}:{})});
 const inputs=[];
 const render=async input=>{inputs.push(input);
  if(defined&&invalidModel)return {puzzles:[{id:puzzle.id,...generated}]};
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
 return {dbPath,pds,operation,taskId,officialRequestId,row,resultRef,calls,inputs,render,validations,validateDefinition,rendererFor:generate?()=>render:noModel};
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

test('legacy manual assembly update preserves existing portal conditions without model authoring',async t=>{
 const portalConditions=[{portalId:'walk-to-well',propRole:'bowl',requiredState:'carried-filled'}];
 const f=fixture(t,'assembly.update',{portalConditions});
 assert.equal((await executeOnce(f)).state,'completed');
 assert.deepEqual(f.inputs,[]);
});

test('v3 one claimed document call publishes mixed typed placements without changing pins, types or attachments',async t=>{
 const f=fixture(t,'assembly.generate',{mixed:true});
 assert.equal((await documentTick(f)).state,'completed');
 assert.equal(f.inputs.length,1);
 assert.equal(f.inputs[0].schemaVersion,'ngs.assembly-generate.v3');
 assert.deepEqual(f.inputs[0].puzzles[1].prop,f.inputs[0].puzzles[1].bindings.spirit);
 assert.equal(f.inputs[0].puzzles[1].intent,'퍼즐 1');
 assert.equal(f.calls.length,3);
});
test('v3 rejects drifted PDS spirit pin or model binding overrides and unreachable placement',async t=>{
 for(const [key,code,stage] of [['wrongPin','ASSEMBLY_PREPARE_INVALID','prepare'],['overridePin','ASSEMBLY_CLI_OUTPUT_INVALID','cli'],['unreachable','ASSEMBLY_CLI_OUTPUT_INVALID','cli']]){
  const f=fixture(t,'assembly.generate',{mixed:true,[key]:true});
  assert.deepEqual(await documentTick(f),{state:'failed',taskId,code,stage,operation:f.operation});
  assert.equal(f.calls.length,2);
  assert.equal(f.inputs.length,stage==='prepare'?0:1);
 }
});
test('v3 typed executor exposes only approved mixed geometry fields',async ()=>{
 const sample={schemaVersion:'ngs.assembly-generate.v3',title:'조립',intent:'공통',map:{},puzzles:[{id:'first'}]};
 const render=createAssemblyRenderer({codexPath:process.execPath,execute:async ({prompt,outputSchema})=>{
  assert.match(prompt,/spirit manifestation/);
  assert.ok(prompt.endsWith(JSON.stringify(sample)));
  assert.deepEqual(outputSchema.properties.puzzles.items.anyOf.map(v=>v.required),
   [['id','layout','attachments'],['id','layout','scale']]);
  return {puzzles:[]};
 }});
 assert.deepEqual(await render(sample),{puzzles:[]});
});

test('v4 waits for the C# validator to approve the model definition before publishing',async t=>{
 const f=fixture(t,'assembly.generate',{defined:true});
 assert.equal((await documentTick(f)).state,'completed');
 assert.equal(f.inputs.length,1);
 assert.equal(f.inputs[0].schemaVersion,'ngs.assembly-generate.v4');
 assert.equal(f.validations.length,1);
 assert.equal(f.calls.length,3);
});
test('v4 fails closed before publish on missing or negative C# proof or missing model definition',async t=>{
 for(const [flag,code,stage] of [['omitValidator','ASSEMBLY_CSHARP_VALIDATOR_UNAVAILABLE','csharp'],
  ['rejectDefinition','ASSEMBLY_CSHARP_VALIDATION_FAILED','csharp'],['invalidModel','ASSEMBLY_CLI_OUTPUT_INVALID','cli']]){
  const f=fixture(t,'assembly.generate',{defined:true,[flag]:true});
  assert.deepEqual(await documentTick(f),{state:'failed',taskId,code,stage,operation:f.operation});
  assert.equal(f.calls.length,2);
 }
});

test('v4 uses provider-safe primitive types and fully required exact object shapes',async ()=>{
 const render=createAssemblyRenderer({codexPath:process.execPath,execute:async ({outputSchema})=>{
  function visit(node){
   if(node.type==='object'){
    assert.deepEqual(Object.keys(node).sort(),['additionalProperties','properties','required','type']);
    assert.equal(node.additionalProperties,false);
    assert.deepEqual(node.required,Object.keys(node.properties));
    Object.values(node.properties).forEach(visit);
   }else if(node.type==='array'){
    assert.deepEqual(Object.keys(node).sort(),['items','type']);visit(node.items);
   }else{
    assert.deepEqual(Object.keys(node),['type']);
    if(Array.isArray(node.type))assert.deepEqual(node.type,['string','null']);
    else assert.ok(['string','integer','boolean'].includes(node.type));
   }
  }
  visit(outputSchema);
  const row=outputSchema.properties.puzzles.items;
  assert.deepEqual(row.required,['id','layout','attachments','definition']);
  const {rules,solution,entities,goal,schemaVersion}=row.properties.definition.properties;
  assert.deepEqual(rules.items.properties.action.properties.item,{type:['string','null']});
  assert.deepEqual(solution.items.properties.item,{type:['string','null']});
  assert.deepEqual(rules.items.properties.module,{type:'string'});
  assert.deepEqual(entities.items.properties.states.items.required,['id','held','payload']);
  assert.deepEqual(goal.required,['role','state']);assert.deepEqual(schemaVersion,{type:'string'});
  return {};
 }});
 await render({schemaVersion:'ngs.assembly-generate.v4',puzzles:[{}]});
});
test('v4 propagates a bounded Codex reason without exposing failure message',async t=>{
 const f=fixture(t,'assembly.generate',{defined:true});
 const renderError=Object.assign(Error('private provider detail'),{codexOutcome:'turn_failed'});
 const result=await documentTick({...f,rendererFor:()=>()=>{throw renderError;}});
 assert.deepEqual(result,{state:'failed',taskId,code:'ASSEMBLY_CLI_FAILED',stage:'cli',
  codexOutcome:'turn_failed',operation:f.operation});
 assert.equal(f.calls.length,2);
});
