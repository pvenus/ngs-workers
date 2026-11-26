import {test} from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {createHash} from 'node:crypto';
import {mkdtempSync,realpathSync,chmodSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {mapCanonical,mapGraphMatches,mapStage,validMapGraph,validMapRequest,validMapState} from './map-contract.mjs';
import {runMapFromPath} from './map-worker.mjs';

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
function fixture(t, operation, payload){
 const dir=realpathSync(mkdtempSync(join(tmpdir(),'map-worker-test-'))),dbPath=join(dir,'planner.sqlite');
 const db=new DatabaseSync(dbPath);chmodSync(dbPath,0o600);
 t.after(()=>{db.close();rmSync(dir,{force:true,recursive:true});});
 db.exec(`CREATE TABLE workflow_runs(official_request_id TEXT PRIMARY KEY,state TEXT,reason TEXT,project_id TEXT,
 request_type TEXT,schema_version TEXT,payload TEXT,payload_sha256 TEXT,registered_at INTEGER);
 CREATE TABLE workflow_tasks(task_id TEXT PRIMARY KEY,official_request_id TEXT,task_input TEXT,task_type TEXT,
 task_key TEXT,worker_type TEXT,depends_on_task_id TEXT,result_ref TEXT,status TEXT,updated_at INTEGER,retry_count INTEGER)`);
 const taskId='workflow.task.'+sha(mapCanonical([officialRequestId,mapStage])),wire=mapCanonical(payload);
 db.prepare("INSERT INTO workflow_runs VALUES(?,'planned',NULL,'binari',?,?,?,?,1)").run(officialRequestId,operation,payload.schemaVersion,wire,sha(wire));
 db.prepare("INSERT INTO workflow_tasks VALUES(?,?,?,?,?,'document',NULL,NULL,'pending',1,0)").run(taskId,officialRequestId,wire,operation,mapStage);
 return {dbPath,taskId,operation,officialRequestId,row:()=>db.prepare('SELECT status,result_ref FROM workflow_tasks').get(),payloadSha256:sha(wire)};
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
 const payload=update({...graph,colliders:[collider]}),f=fixture(t,'map.update',payload);
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

test('map.generate preserves absent/empty optional colliders and does not expose them to coordinate model',async t=>{
 for(const saved of [graph,{...graph,colliders:[]}]){
  const payload=generate(saved),f=fixture(t,'map.generate',payload);
  const resultRef='binari/cinematics/maps/'+officialRequestId+'/1.json';
  const run=await runMapFromPath({...f,pds:{
   async readMap(){return {backgrounds:graph.regions.map(r=>({id:r.backgroundId,title:r.name,sha256:r.imageSha256}))};},
   async readMapImage(){return {sourceSha256:sha(bytes),title:'fixture',base64:bytes.toString('base64')};},
   async publishMap(_claim,value){assert.equal(mapCanonical(value),mapCanonical(saved));return {ok:true,resultRef};}
  },render(input,{imageBytes}){
   assert.equal(Object.hasOwn(input,'colliders'),false);
   assert.equal(imageBytes.length,1);
   const [{id,hit,spawn}]=graph.connections;
   return {coordinates:[{id,hit,spawn}]};
  }});
  assert.deepEqual(run,{state:'completed',taskId:f.taskId,resultRef});
 }
});
