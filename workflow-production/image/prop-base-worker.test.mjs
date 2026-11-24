import test from 'node:test';
import assert from 'node:assert/strict';
import {chmodSync,mkdtempSync,realpathSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {PlannerStore} from './vendor/planner-f24d23a/planner.mjs';
import {canonical,sha} from './vendor/prop-composite-v1/envelope.mjs';
import {buildPropGeneratePlan,propRef,propKeys} from './vendor/prop-composite-v1/prop-generate-plan.mjs';
import {runPropBaseFromPath,nextPropBaseFromPath} from './prop-base-worker.mjs';
import {imageTick,parseImageArgs} from './image-runner.mjs';
import {localPorts,propBasePath} from './local-ports.mjs';

const template=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR4nGNgAAIAAAUAAXpeqz8AAAAASUVORK5CYII=','base64');
const png=Buffer.from(template);png.writeUInt32BE(72,16);png.writeUInt32BE(96,20);
const masterFields=[
 {id:'visualStyle',text:JSON.stringify({schemaVersion:'ngs.character-style-contract.v1',styleBlock:'sealed style'})},
 {id:'visualProhibitions',text:JSON.stringify({schemaVersion:'ngs.character-style-constraints.v1',renderingExclusions:['frame']})},
].map(f=>({...f,sha256:sha(f.text)}));
const markdown='# 프랍 디자인 설계\n\n'+['형태와 식별','재료와 색','상태 사이의 일관성','납품과 검수'].map(k=>'## '+k+'\n내용').join('\n');
const sources=[{role:'master-design',categoryId:'expression',sha256:sha(canonical(masterFields)),fields:masterFields},
 {role:'prop-design',categoryId:'category.add.26710502f5713d99018b77a29384d5da',sha256:sha(markdown),markdown}];
const contextSha256=sha(canonical(sources.map(x=>[x.role,x.categoryId,x.sha256])));
const context={schemaVersion:'ngs.prop-image-design.v1',projectId:'binari',sources,contextSha256};
function fixture(t,operation='prop.generate',mode='valid'){
 const root=realpathSync(mkdtempSync(join(tmpdir(),'prop-base-fixture-'))),dbPath=join(root,'w.sqlite');
 const store=new PlannerStore(dbPath);chmodSync(dbPath,0o600);
 const db=store.db;
 db.exec('ALTER TABLE workflow_tasks ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0 CHECK(retry_count BETWEEN 0 AND 3)');
 t.after(()=>{store.close();rmSync(root,{recursive:true,force:true});});
 const officialRequestId='request.official.'+sha(operation),schemaVersion='ngs.'+operation.replace('.','-')+'.v1';
 const payload={schemaVersion,projectId:'binari',propId:'12.candle',designContextSha256:contextSha256,
  intent:'raw request should not be model input',...(operation==='prop.regenerate'?{source:{revision:3,sha256:'a'.repeat(64)}}:{})};
 const payloadSha256=sha(canonical(payload));
 const artifact={schemaVersion:'ngs.prop-design-spec.v1',propId:payload.propId,designContextSha256:contextSha256,
  title:'촛불',appearancePrompt:'exact approved game prop, clear design',motion:'still',motionPrompt:null,
  interaction:{pixelOpen:null,detailTrigger:null}};
 const designRef=propRef(propKeys.design,sha(canonical(artifact)));
 const [design,base,...remaining]=buildPropGeneratePlan({officialRequestId,requestType:operation,payload});
 db.prepare(`INSERT INTO workflow_runs VALUES(?,?,?,?,?,?,1,'planned',2,NULL)`)
  .run(officialRequestId,'binari',operation,schemaVersion,canonical(payload),payloadSha256);
 for(const row of [design,base,...remaining])db.prepare(`INSERT INTO workflow_tasks
   (task_id,official_request_id,task_key,task_type,worker_type,task_input,result_ref,updated_at,depends_on_task_id,status)
   VALUES(?,?,?,?,?,?,?,2,?,?)`)
  .run(row.id,officialRequestId,row.key,row.type,row.workerType,canonical(row.input),
   row===design&&mode!=='blocked'?designRef:null,row.dependsOn,row===design&&mode!=='blocked'?'completed':'pending');
 const state=()=>db.prepare('SELECT * FROM workflow_tasks WHERE task_id=?').get(base.id);
 const calls=[],options={dbPath,taskId:base.id,officialRequestId,operation:base.type,signal:new AbortController().signal,
  providerReady:async()=>true,mode:'run',
  renderProp:async input=>{
   calls.push(['render',input]);
   if(mode==='revoked') db.prepare("UPDATE workflow_tasks SET result_ref=? WHERE task_id=?").run('newer',design.id);
   if(mode==='bad png')return Buffer.alloc(2);
   return png;
  },
  pds:localPorts({transport:async r=>{
   calls.push(['http',r.method,r.path]);
   if(r.method==='GET')return {enabled:true,schemaVersion:r.path===propBasePath?'workflow.prop-stage.v1':'workflow.representative-portrait-write.v1'};
   assert.deepEqual(r.body.claim,{officialRequestId,taskId:base.id,projectId:'binari',payloadSha256,claimStamp:state().updated_at});
   if(r.path===propBasePath+'/read'){
    assert.deepEqual(Object.keys(r.body),['claim']);
    return {design:{officialRequestId,taskId:design.id,key:design.key,resultRef:designRef,
     artifact:mode==='wrong design'?{...artifact,appearancePrompt:'tampered'}:artifact,lineage:null},context};
   }
   assert.equal(r.path,propBasePath);
   assert.deepEqual(r.body.artifact,{media:{sha256:sha(png),bytes:png.length,width:72,height:96,mediaType:'image/png'},base64:png.toString('base64')});
   return {ok:true,resultRef:mode==='wrong ack'?'bogus':propRef(propKeys.base,sha(png))};
  }})};
 return {options,state,db,base,design,calls,artifact};
}
for(const type of ['prop.generate','prop.regenerate'])test(type+' runs one approved image in existing resident image tick',async t=>{
 const f=fixture(t,type);
 assert.deepEqual({...nextPropBaseFromPath(f.options.dbPath)},{taskId:f.base.id,officialRequestId:f.options.officialRequestId,operation:f.base.type});
 const r=await imageTick(f.options);
 assert.equal(r.state,'completed');
 assert.equal(f.state().result_ref,propRef(propKeys.base,sha(png)));
 const render=f.calls.filter(c=>c[0]==='render');
 assert.equal(render.length,1);
 assert.deepEqual(render[0][1],{projectId:'binari',propId:'12.candle',state:'base',lane:'detail',
  intent:f.artifact.appearancePrompt,sources,contextSha256});
 assert.equal(parseImageArgs(['--execute','--db',f.options.dbPath,'--official-request-id',f.options.officialRequestId,'--task-id',
  f.base.id,'--operation',f.base.type]).operation,f.base.type);
 assert.equal((await imageTick(f.options)).state,'idle');
});
for(const mode of ['blocked','wrong design','bad png','wrong ack','revoked'])test(mode+' never falls back or marks uncertain publication as failed',async t=>{
 const f=fixture(t,'prop.generate',mode);
 const r=await runPropBaseFromPath(f.options);
 const published=f.calls.some(c=>c[0]==='http'&&c[1]==='POST'&&c[2]===propBasePath);
 switch(mode){
 case 'blocked': assert.equal(r.state,'not_claimable');assert.equal(f.state().status,'pending');break;
 case 'wrong design':
 case 'bad png': assert.equal(r.state,'failed');assert.equal(f.state().status,'failed');break;
 case 'revoked':
 case 'wrong ack': assert.equal(r.state,'requires_reconciliation');assert.equal(f.state().status,'running');break;
 }
 assert.equal(published,mode==='wrong ack');
 if(mode==='wrong design'||mode==='blocked')assert.equal(f.calls.some(c=>c[0]==='render'),false);
});
test('only successful exact aged claim increments retry, cap blocks re-entry',async t=>{
 const f=fixture(t);
 f.db.prepare("UPDATE workflow_tasks SET updated_at=?,status='failed',retry_count=3 WHERE task_id=?").run(Date.now()-601000,f.base.id);
 assert.equal((await runPropBaseFromPath(f.options)).state,'not_claimable');
 assert.equal(f.state().retry_count,3);
 f.db.prepare("UPDATE workflow_tasks SET retry_count=2 WHERE task_id=?").run(f.base.id);
 assert.equal((await runPropBaseFromPath(f.options)).state,'completed');
 assert.equal(f.state().retry_count,3);
});
