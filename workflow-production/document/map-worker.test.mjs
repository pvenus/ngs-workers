import {test} from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {createHash} from 'node:crypto';
import {mkdtempSync,realpathSync,chmodSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {mapCanonical,mapGraphMatches,mapStage,mapDocumentStages,mapGenerateDesignSchema,validMapGraph,validMapRequest,validMapState} from './map-contract.mjs';
import {runMapFromPath,createMapRenderer} from './map-worker.mjs';

const sha=value=>createHash('sha256').update(value).digest('hex');
const officialRequestId='request.official.'+'f'.repeat(64), mapId='request.official.'+'e'.repeat(64);
const bytes=Buffer.from('89504e470d0a1a0a','hex');
const graph={
 title:'Ritual yard',startRegionId:'north',
 regions:['north','south'].map((id,i)=>({id,name:id,description:'Area',backgroundId:'background.'+String(i+1).repeat(64),imageSha256:sha(bytes)})),
 connections:[{id:'door',from:'north',to:'south',hit:{x:0,y:10,width:20,height:20},spawn:{x:300,y:400},label:'Next'}]
};
const collider={id:'wall',regionId:'north',rect:{x:450,y:450,width:550,height:550}};
const update=graph=>({schemaVersion:'ngs.map-update.v1',projectId:'binari',graph,mapId,expectedRevision:2});
const generate=graph=>({schemaVersion:'ngs.map-generate.v1',projectId:'binari',
 graph:{...graph,connections:graph.connections.map(e=>({...e,hit:null,spawn:null}))},intent:'Find the doorway.'});
const design=(collisionMode='suggest')=>({startNotes:'North path',portalNotes:'Threshold',collisionNotes:'Avoid the gate',collisionMode});
const generateV2=(v,mode)=>({...generate(v),schemaVersion:mapGenerateDesignSchema,design:design(mode)});
function fixture(t, operation, payload){
 const dir=realpathSync(mkdtempSync(join(tmpdir(),'map-worker-test-'))),dbPath=join(dir,'planner.sqlite');
 const db=new DatabaseSync(dbPath);chmodSync(dbPath,0o600);
 t.after(()=>{db.close();rmSync(dir,{force:true,recursive:true});});
 db.exec(`CREATE TABLE workflow_runs(official_request_id TEXT PRIMARY KEY,state TEXT,reason TEXT,project_id TEXT,
 request_type TEXT,schema_version TEXT,payload TEXT,payload_sha256 TEXT,registered_at INTEGER);
 CREATE TABLE workflow_tasks(task_id TEXT PRIMARY KEY,official_request_id TEXT,task_input TEXT,task_type TEXT,
 task_key TEXT,worker_type TEXT,depends_on_task_id TEXT,result_ref TEXT,status TEXT,updated_at INTEGER,retry_count INTEGER,map_part_json TEXT)`);
 const keys=payload.schemaVersion===mapGenerateDesignSchema?mapDocumentStages:[mapStage];
 const ids=keys.map(key=>'workflow.task.'+sha(mapCanonical([officialRequestId,key]))),wire=mapCanonical(payload);
 db.prepare("INSERT INTO workflow_runs VALUES(?,'planned',NULL,'binari',?,?,?,?,1)").run(officialRequestId,operation,payload.schemaVersion,wire,sha(wire));
 for(const [i,key] of keys.entries())db.prepare("INSERT INTO workflow_tasks VALUES(?,?,?,?,?,'document',?,NULL,'pending',1,0,NULL)")
   .run(ids[i],officialRequestId,wire,operation,key,ids[i-1]??null);
 return {dbPath,taskId:ids[0],ids,operation,officialRequestId,
  row:(i=0)=>db.prepare('SELECT status,result_ref,map_part_json FROM workflow_tasks WHERE task_id=?').get(ids[i]),payloadSha256:sha(wire)};
}

test('map collision edits accept old graphs, enforce bounded exact shape and preserve graph bindings',()=>{
 assert.equal(validMapGraph(graph),true);
 assert.equal(validMapRequest(generate(graph),'map.generate'),true);
 const edited={...graph,colliders:[collider]};
 assert.equal(validMapRequest(update(edited),'map.update'),true);
 assert.equal(validMapState({schemaVersion:'ngs.map-metadata.v1',projectId:'binari',mapId,officialRequestId,
  revision:3,updatedAt:1,graph:edited}),true);
 assert.equal(validMapRequest(generate({...graph,colliders:[]}),'map.generate'),true);
 assert.equal(validMapRequest(generate(edited),'map.generate'),false);
 assert.equal(mapGraphMatches(edited,structuredClone(edited)),true);
 assert.equal(mapGraphMatches(edited,{...graph,colliders:[]}),false);
 for(const colliders of [null,undefined,[collider,collider],[{...collider,id:'Bad'}],[{...collider,regionId:'unknown'}],
  [{...collider,rect:{...collider.rect,width:551}}],[{...collider,rect:{...collider.rect,height:0.5}}],
  [{...collider,label:'Unexpected'}],Array.from({length:97},(_,i)=>({...collider,id:'wall-'+i}))]){
  assert.equal(validMapGraph({...graph,colliders}),false);
 }
});

test('map.update publishes exact collider edits without running the model; validates result receipt',async t=>{
 const payload=update({...graph,colliders:[collider,{...collider,id:"round",shape:"ellipse",rotation:51}]}),f=fixture(t,'map.update',payload);
 const resultRef='binari/cinematics/maps/'+mapId+'/3.json';
 const unexpected=()=>assert.fail('Map update must not run model or fetch images.');
 const run=await runMapFromPath({...f,render:unexpected,pds:{readMap:unexpected,readMapImage:unexpected,
  async publishMap(claim,value){
   assert.equal(claim.payloadSha256,f.payloadSha256);
   assert.equal(mapCanonical(value),mapCanonical(payload.graph));
   return {ok:true,resultRef};
  }}});
 assert.deepEqual(run,{state:'completed',taskId:f.taskId,resultRef});
 assert.equal(f.row().result_ref,resultRef);

 const malformed=fixture(t,'map.update',payload);
 const invalid=await runMapFromPath({...malformed,render:unexpected,pds:{async publishMap(){return {ok:true,resultRef:'unexpected'};}}});
 assert.equal(invalid.code,'MAP_SAVE_NOT_CONFIRMED');
 assert.equal(malformed.row().result_ref,null);
});

const fixtures=()=>({
 async readMap(){return {backgrounds:graph.regions.map(r=>({id:r.backgroundId,title:r.name,sha256:r.imageSha256}))};},
 async readMapImage(){return {sourceSha256:sha(bytes),title:'fixture',base64:bytes.toString('base64')};},
});
const startPoint={x:322,y:733};
const validCollider={...collider,shape:'ellipse',rotation:30};
const stageResult=(input,colliders)=>{
 if(input.stage==='start')return {startRegionId:input.startRegionId,startPoint};
 if(input.stage==='portals'){
  const [{id,hit,spawn}]=graph.connections;return {coordinates:[{id,hit,spawn}]};
 }
 return {colliders};
};
test('v2 has three independently claimed tasks and no publication before the last',async t=>{
 for(const [mode,collisions] of [['none',[]],['suggest',[validCollider]]]){
  const f=fixture(t,'map.generate',generateV2(graph,mode)), resultRef='binari/cinematics/maps/'+officialRequestId+'/1.json', calls=[];
  const input={...f,pds:{...fixtures(),
   async publishMap(_claim,value){
    assert.deepEqual(calls,['start','portals','collisions']);
    assert.equal(mapCanonical(value),mapCanonical({...graph,startPoint,colliders:collisions}));
    return {ok:true,resultRef};
   }
  },render(model,{imageBytes}){
   calls.push(model.stage);
   assert.equal(imageBytes.length,1);assert.equal(model.attachmentCount,1);
   if(model.stage==='start'){assert.equal(model.region.id,graph.startRegionId);assert.equal(model.notes,design().startNotes);}
   if(model.stage==='portals'){assert.deepEqual(model.connections.map(e=>e.hit),[null]);assert.equal(model.notes,design().portalNotes);}
   if(model.stage==='collisions'){assert.deepEqual(model.connections.map(e=>e.hit),[graph.connections[0].hit]);assert.equal(model.mode,mode);assert.deepEqual(model.initialPoint,startPoint);}
   return stageResult(model,collisions);
  }};
  assert.equal((await runMapFromPath({...input,taskId:f.ids[2]})).state,'not_claimable');
  for(const [i,taskId] of f.ids.entries()){
   const run=await runMapFromPath({...input,taskId});
   assert.equal(run.state,'completed');assert.equal(run.taskId,taskId);
   assert.equal(f.row(i).result_ref,run.resultRef);
   if(i<2)assert.match(run.resultRef,/^workflow\.map-stage\.[a-f0-9]{64}$/);
   else assert.equal(run.resultRef,resultRef);
  }
 }
});
test('legacy v1 generation still calls only the portal stage',async t=>{
 const f=fixture(t,'map.generate',generate(graph));
 const result=await runMapFromPath({...f,pds:{...fixtures(),async publishMap(_claim,value){
   assert.deepEqual(value,graph);return {ok:true,resultRef:'binari/cinematics/maps/'+officialRequestId+'/1.json'};
 }},render(input){assert.equal(input.stage,'portals');return stageResult(input,[]);}});
 assert.equal(result.state,'completed');
});
test('invalid stage data does not publish, and committed predecessor stages remain done',async t=>{
 for(const [stage,bad,code,mode] of [
  ['start',{startRegionId:'other',startPoint},'MAP_START_CONFIRMATION_INVALID'],
  ['start',{startRegionId:graph.startRegionId,startPoint:{x:1001,y:850}},'MAP_START_CONFIRMATION_INVALID'],
  ['portals',{coordinates:[]},'MAP_MODEL_COORDINATES_INVALID'],
  ['collisions',{colliders:[{...validCollider,rotation:400}]},'MAP_COLLIDERS_INVALID'],
  ['collisions',{colliders:[validCollider]},'MAP_COLLIDERS_INVALID','none'],
  ['collisions',null,'MAP_COLLISIONS_CLI_FAILED'],
 ]){
  const f=fixture(t,'map.generate',generateV2(graph,mode)),stages=['start','portals','collisions'];
  for(let i=0;i<=stages.indexOf(stage);i++){
   const result=await runMapFromPath({...f,taskId:f.ids[i],pds:{...fixtures(),publishMap:()=>assert.fail('must not publish partial map')},
    render(input){if(input.stage===stage){if(bad===null)throw Error('fixture failed');return bad;}return stageResult(input,[]);}});
   if(stages[i]===stage){assert.equal(result.code,code);assert.equal(result.state,'failed');}
   else assert.equal(result.state,'completed');
  }
 }
});
test('three CLI calls use distinct guides and strict outputs',async()=>{
 const invocations=[];
 const renderer=createMapRenderer({codexPath:process.execPath,execute:async input=>{invocations.push(input);return {};}});
 for(const stage of ['start','portals','collisions'])
  await renderer({stage,attachmentCount:1},{imageBytes:[bytes]});
 assert.deepEqual(invocations.map(x=>Object.keys(x.outputSchema.properties)[0]),['startRegionId','coordinates','colliders']);
 assert.equal(new Set(invocations.map(x=>x.prompt.split('\n')[0])).size,3);
});
