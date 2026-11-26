import {test} from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {mkdtempSync,realpathSync,chmodSync,rmSync,writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {canonical,sha,propDesignTaskId,propDesignResultRef,expectedInput,propCategory,propDesignPath} from './prop-design-contract.mjs';
import {runPropDesignFromPath} from './prop-design-worker.mjs';
import {createPropDesignRenderer} from './prop-design-renderer.mjs';
import {localPorts} from './local-ports.mjs';
import {documentTick} from './runner.mjs';

const dirSignal=()=>new AbortController().signal;
const model={title:'놋쇠 등잔',appearancePrompt:'정면, 검은 놋쇠 받침과 열린 심지, 비나리 그림체.',motionPrompt:null,
 interaction:{pixelOpen:{xPermille:220,yPermille:280,widthPermille:500,heightPermille:450},detailTrigger:null}};
function evidence(){
 const fields=[
  {id:'visualStyle',text:'{"schemaVersion":"ngs.character-style-contract.v1","rule":"한지의 재료 느낌"}'},
  {id:'visualProhibitions',text:'{"schemaVersion":"ngs.character-style-constraints.v1","rule":"문자와 사진 배제"}'}
 ].map(x=>({...x,sha256:sha(x.text)}));
 const markdown='# 프랍 디자인 설계\n\n## 형태와 식별\n분명한 외곽선\n\n## 재료와 색\n먹색\n\n## 상태 사이의 일관성\n형태 유지\n\n## 납품과 검수\n투명 추출\n';
 const sources=[{role:'master-design',categoryId:'expression',sha256:sha(canonical(fields)),fields},
  {role:'prop-design',categoryId:propCategory,sha256:sha(markdown),markdown}];
 return {schemaVersion:'ngs.prop-image-design.v1',projectId:'binari',sources,
  contextSha256:sha(canonical(sources.map(s=>[s.role,s.categoryId,s.sha256])))};
}
function fixture(t,{kind='generate',motion,parent=null,wireEdit=d=>d,status='pending',age=0,retry=0}={}){
 const dir=realpathSync(mkdtempSync(join(tmpdir(),'prop-doc-test-'))),dbPath=join(dir,'planner.sqlite');
 const db=new DatabaseSync(dbPath);chmodSync(dbPath,0o600);
 db.exec(`CREATE TABLE workflow_runs(official_request_id TEXT PRIMARY KEY,state TEXT,reason TEXT,project_id TEXT,
 request_type TEXT,schema_version TEXT,payload TEXT,payload_sha256 TEXT,registered_at INTEGER);
 CREATE TABLE workflow_tasks(task_id TEXT PRIMARY KEY,official_request_id TEXT,task_input TEXT,task_type TEXT,
 task_key TEXT,worker_type TEXT,depends_on_task_id TEXT,result_ref TEXT,status TEXT,updated_at INTEGER,retry_count INTEGER)`);
 const context=evidence(),officialRequestId='request.official.'+'f'.repeat(64),taskId=propDesignTaskId(officialRequestId);
 const reqType='prop.'+kind,operation=reqType+'.design';
 const payload={schemaVersion:'ngs.prop-'+kind+'.v1',projectId:'binari',propId:'brass-lamp',intent:'검은 놋쇠 등잔 하나',
  designContextSha256:context.contextSha256,...(motion?{motion}:{}),...(kind==='regenerate'?{source:{revision:7,sha256:'a'.repeat(64)}}:{})};
 const input=expectedInput(payload,operation),wire=canonical(payload);
 db.prepare("INSERT INTO workflow_runs VALUES(?,'planned',NULL,'binari',?,?,?,?,1)").run(officialRequestId,reqType,payload.schemaVersion,wire,sha(wire));
 db.prepare("INSERT INTO workflow_tasks VALUES(?,?,?,?,'prop_design','document',?,NULL,?,?,?)")
  .run(taskId,officialRequestId,canonical(wireEdit(input)),operation,parent,status,Date.now()-age,retry);
 t.after(()=>{db.close();rmSync(dir,{force:true,recursive:true});});
 return {db,dbPath,operation,officialRequestId,taskId,context,input,payload,wire,
  row:()=>db.prepare('SELECT * FROM workflow_tasks WHERE task_id=?').get(taskId)};
}

test('resident document tick claims both exact official create and regenerate; stage publish is typed and one result',async t=>{
 for(const kind of ['generate','regenerate']){
  const f=fixture(t,{kind,motion:kind==='regenerate'?'subtle':undefined});
  const calls=[];
  const pds={
   async capability(_s,op){calls.push('capability');return op===f.operation;},
   async readPropDesign(claim){
    calls.push('read');assert.equal(f.row().status,'running');
    assert.deepEqual(claim,{officialRequestId:f.officialRequestId,taskId:f.taskId,projectId:'binari',
     payloadSha256:sha(f.wire),claimStamp:f.row().updated_at});
    return {input:f.input,context:f.context};
   },
   async publishPropDesign(_claim,artifact){
    calls.push('publish');assert.equal(artifact.schemaVersion,'ngs.prop-design-spec.v1');
    assert.equal(artifact.motion,f.input.motion);
    assert.equal(artifact.designContextSha256,f.context.contextSha256);
    assert.equal(artifact.motionPrompt,f.input.motion==='still'?null:'심지만 흔들림');
    assert.equal(artifact.propId,f.payload.propId);
    assert.equal(Object.hasOwn(artifact,'source'),false);
    return {ok:true,resultRef:propDesignResultRef(artifact)};
   }
  };
  const r=await documentTick({dbPath:f.dbPath,pds,rendererFor:op=>{
   assert.equal(op,f.operation);
   return async (data)=>{
    calls.push('model');
    assert(!Object.hasOwn(data,'source'));assert.equal(data.intent,f.payload.intent);
    assert.deepEqual(data.sources,f.context.sources);
    return {...model,motionPrompt:data.motion==='still'?null:'심지만 흔들림'};
   };
  },signal:dirSignal()});
  assert.equal(r.state,'completed');assert.equal(r.resultRef,f.row().result_ref);
  assert.deepEqual(calls,['capability','read','model','publish']);
  assert.equal((await documentTick({dbPath:f.dbPath,pds,rendererFor:()=>{throw Error('unexpected duplicate');},signal:dirSignal()})).state,'idle');
 }
});

test('predecessor and exact task input, current stamp and paired context pins stop unauthorized render',async t=>{
 const p=()=>{throw Error('must not call');};
 const blocked=fixture(t,{parent:'wrong-parent'});
 assert.equal((await runPropDesignFromPath({...blocked,pds:{readPropDesign:p,publishPropDesign:p},render:p})).state,'not_claimable');
 const mismatched=fixture(t,{kind:'regenerate',wireEdit:v=>({...v,source:{...v.source,revision:8}})});
 await assert.rejects(runPropDesignFromPath({...mismatched,pds:{readPropDesign:p,publishPropDesign:p},render:p}),/BINDING_INVALID/);
 assert.equal(mismatched.row().status,'pending');
 for(const revoke of [false,true]){
  const f=fixture(t);
  const r=await runPropDesignFromPath({...f,pds:{readPropDesign:async()=>{
   if(revoke)f.db.exec("UPDATE workflow_runs SET state='cancelled'");
   else f.context.sources[0].fields[0].text+=' forged';
   return {context:f.context,input:f.input};
  },publishPropDesign:p},render:p});
  assert.equal(r.state,revoke?'requires_reconciliation':'failed');
  assert.equal(f.row().status,revoke?'running':'failed');
 }
});
test('only bounded output can publish; send/commit uncertainty keeps same running attempt',async t=>{
 for(const test of ['rectangle','script','planned-changed','wrong-receipt','uncertain-post']){
  const f=fixture(t),hits=[];
  const r=await runPropDesignFromPath({...f,pds:{readPropDesign:async()=>({context:f.context,input:f.input}),
   async publishPropDesign(_claim,a){
    hits.push('publish');
    if(test==='planned-changed')f.db.exec("UPDATE workflow_tasks SET task_input='{}'");
    if(test==='wrong-receipt')return {ok:true,resultRef:'prop.design-spec.'+'0'.repeat(64)};
    if(test==='uncertain-post')throw Error('PDS_WAIT');
    return {ok:true,resultRef:propDesignResultRef(a)};
   }},
   render:async()=>test==='rectangle'?{...model,interaction:{...model.interaction,pixelOpen:{...model.interaction.pixelOpen,xPermille:900}}}:
    test==='script'?{...model,script:'run arbitrary execution'}:model});
  const invalid=test==='rectangle'||test==='script';
  assert.equal(r.state,invalid?'failed':'requires_reconciliation');
  assert.equal(f.row().status,invalid?'failed':'running');
  assert.deepEqual(hits,invalid?[]:['publish']);
  if(!invalid)assert.equal((await runPropDesignFromPath({...f,pds:{readPropDesign(){throw Error()},publishPropDesign(){throw Error()}},render(){throw Error()}})).state,'not_claimable');
 }
});
test('readiness is explicit, transport scoped, and Codex prompt carries only verified passages',async t=>{
 const f=fixture(t);
 const noRuntime=()=>{};noRuntime.ready=()=>false;
 assert.equal((await runPropDesignFromPath({...f,pds:{readPropDesign(){throw Error()},publishPropDesign(){throw Error()}},
  render:noRuntime})).state,'not_ready');
 assert.equal(f.row().status,'pending');
 const calls=[],pds=localPorts({transport:async opts=>{
  calls.push(opts);
  if(opts.method==='GET')return {enabled:true,schemaVersion:'workflow.prop-design-write.v1'};
  return {};
 }});
 const s=dirSignal(),claim={taskId:f.taskId};
 assert.equal(await pds.capability(s,'prop.regenerate.design'),true);
 await pds.readPropDesign(claim,s);await pds.publishPropDesign(claim,model);
 assert.deepEqual(calls.map(c=>[c.path,c.method]),[[propDesignPath,'GET'],[propDesignPath+'/read','POST'],[propDesignPath,'POST']]);
 assert.deepEqual(calls[2].body,{claim,artifact:model});assert.equal(calls[2].signal,undefined);
 const r=createPropDesignRenderer({execute:async({prompt,outputSchema})=>{
  assert.match(prompt,/sources/u);assert.match(prompt,/Do not run tools/);assert.match(prompt,/등잔/u);
  assert.equal(outputSchema.properties.motionPrompt.type,'null');
  assert.equal(outputSchema.additionalProperties,false);return model;
 }});
 await r({motion:'still',sources:f.context.sources,propId:f.payload.propId,intent:f.payload.intent});
});

test('typed document renderer reuses the actual bounded CLI transport against an offline executable fixture',async t=>{
 const root=realpathSync(mkdtempSync(join(tmpdir(),'prop-doc-codex-fixture-')));
 t.after(()=>rmSync(root,{force:true,recursive:true}));
 const binary=join(root,'codex-fixture');
 writeFileSync(binary,`#!/opt/homebrew/bin/python3.12
import json,sys,uuid
def send(msg):print(json.dumps(msg),flush=True)
for line in sys.stdin:
 x=json.loads(line); m=x['method']; p=x.get('params',{})
 if m=='initialized':continue
 result={}
 if m=='thread/start':
  assert p['sandbox']=='read-only' and p['ephemeral']
  tid=str(uuid.uuid4()); result={'thread':{'id':tid}}
 if m=='thread/unsubscribe':result={'status':'unsubscribed'}
 if m=='turn/start':result={'turn':{'id':'work'}}
 send({'id':x['id'],'result':result})
 if m=='turn/start':
  assert p['outputSchema']['properties']['motionPrompt']['type']=='null'
  output=json.loads(${JSON.stringify(JSON.stringify(model))})
  send({'method':'item/completed','params':{'threadId':tid,'turnId':'work','item':{'type':'agentMessage','text':json.dumps(output)}}})
  send({'method':'turn/completed','params':{'threadId':tid,'turn':{'id':'work','status':'completed'}}})
 `,{mode:0o700});
 const renderer=createPropDesignRenderer({codexPath:binary,timeoutMs:5000});
 assert.equal(renderer.ready(),true);
 const out=await renderer({motion:'still',sources:evidence().sources,propId:'brass-lamp',intent:'등잔'},{signal:dirSignal()});
 assert.deepEqual(out,model);
 const cancelled=new AbortController();cancelled.abort();
 await assert.rejects(renderer({motion:'still'},{signal:cancelled.signal}),/CODEX_CANCELLED/);
});
