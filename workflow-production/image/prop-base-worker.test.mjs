import test from 'node:test';
import assert from 'node:assert/strict';
import {chmodSync,mkdtempSync,realpathSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {crc32,deflateSync} from 'node:zlib';
import {PlannerStore} from './vendor/planner-f24d23a/planner.mjs';
import {canonical,sha} from './vendor/prop-composite-v1/envelope.mjs';
import {buildPropGeneratePlan,propRef,propKeys} from './vendor/prop-composite-v1/prop-generate-plan.mjs';
import {runPropBaseFromPath,nextPropBaseFromPath} from './prop-base-worker.mjs';
import {imageTick,parseImageArgs} from './image-runner.mjs';
import {localPorts,propBasePath} from './local-ports.mjs';

const u32=n=>{const bytes=Buffer.alloc(4);bytes.writeUInt32BE(n);return bytes;};
const chunk=(type,data)=>{const block=Buffer.concat([Buffer.from(type),data]);return Buffer.concat([u32(data.length),block,u32(crc32(block))]);};
function basePng({width=1536,height=1024,alpha=false,transparentChunk=false}={}){
 const channels=alpha?4:3;
 const row=Buffer.alloc(1+width*channels);for(let x=0;x<width;x++)row[1+x*channels+1]=255;
 return Buffer.concat([Buffer.from('89504e470d0a1a0a','hex'),
  chunk('IHDR',Buffer.concat([u32(width),u32(height),Buffer.from([8,alpha?6:2,0,0,0])])),
  ...(transparentChunk?[chunk('tRNS',Buffer.alloc(6))]:[]),
  chunk('IDAT',deflateSync(Buffer.concat(Array(height).fill(row)))),chunk('IEND',Buffer.alloc(0))]);
}
const png=basePng();
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
 const reference=mode.startsWith('reference')?{propId:'original-prop',revision:1,sha256:'c'.repeat(64)}:null;
 const payload={schemaVersion,projectId:'binari',propId:'12.candle',designContextSha256:contextSha256,
  intent:'raw request should not be model input',...(mode==='spirit'?{presentationProfile:'spirit-v1'}:mode.startsWith('pixel-v2')?{presentationProfile:'spirit-v2'}:{}),...(reference?{reference}:{}),...(operation==='prop.regenerate'?{source:{revision:3,sha256:'a'.repeat(64)}}:{})};
 const payloadSha256=sha(canonical(payload));
 const artifact={schemaVersion:'ngs.prop-design-spec.v1',propId:payload.propId,designContextSha256:contextSha256,
  title:'촛불',appearancePrompt:'exact approved game prop, clear design',motion:'still',motionPrompt:null,
  interaction:{pixelOpen:null,detailTrigger:null}};
 const designRef=propRef(propKeys.design,sha(canonical(artifact)));
 const [design,initialBase,...remaining]=buildPropGeneratePlan({officialRequestId,requestType:operation,payload});
 const pixel=mode.startsWith('pixel-v2'),base=pixel?remaining.find(row=>row.key===propKeys.pixelSource):initialBase;
 const detail=remaining.find(row=>row.key===propKeys.detail),detailPng=basePng({alpha:true});
 const prior=new Map([[design,mode==='blocked'?null:designRef],...(pixel?
  [[initialBase,propRef(propKeys.base,sha(png))],[detail,propRef(propKeys.detail,sha(detailPng))]]:[])]);
 db.prepare(`INSERT INTO workflow_runs VALUES(?,?,?,?,?,?,1,'planned',2,NULL)`)
  .run(officialRequestId,'binari',operation,schemaVersion,canonical(payload),payloadSha256);
 for(const row of [design,initialBase,...remaining])db.prepare(`INSERT INTO workflow_tasks
   (task_id,official_request_id,task_key,task_type,worker_type,task_input,result_ref,updated_at,depends_on_task_id,status)
   VALUES(?,?,?,?,?,?,?,2,?,?)`)
  .run(row.id,officialRequestId,row.key,row.type,row.workerType,canonical(row.input),
   prior.get(row)??null,row.dependsOn,prior.get(row)?'completed':'pending');
 const state=()=>db.prepare('SELECT * FROM workflow_tasks WHERE task_id=?').get(base.id);
 const calls=[],options={dbPath,taskId:base.id,officialRequestId,operation:base.type,signal:new AbortController().signal,
  providerReady:async()=>true,mode:'run',
  renderProp:async input=>{
   calls.push(['render',input]);
   if(mode==='revoked') db.prepare("UPDATE workflow_tasks SET result_ref=? WHERE task_id=?").run('newer',design.id);
   if(mode==='cli blocked')throw Error('WORKFLOW_IMAGE_CLI_REPORTED_BLOCKED');
   if(mode==='native wrong canvas')throw Error('WORKFLOW_PROP_BASE_PNG_CANVAS');
   if(mode==='private message')throw Error('WORKFLOW_PROP_BASE_PRIVATE_TEXT');
   if(mode==='bad png')return Buffer.alloc(2);
   if(mode==='alpha')return basePng({alpha:true});
   if(mode==='wrong canvas')return basePng({width:8,height:352});
   if(mode==='implicit alpha')return basePng({transparentChunk:true});
   return png;
  },
  pds:localPorts({transport:async r=>{
   calls.push(['http',r.method,r.path]);
   if(r.method==='GET')return {enabled:true,schemaVersion:r.path===propBasePath?'workflow.prop-stage.v1':'workflow.representative-portrait-write.v1'};
   assert.deepEqual(r.body.claim,{officialRequestId,taskId:base.id,projectId:'binari',payloadSha256,claimStamp:state().updated_at});
   if(r.path===propBasePath+'/read'){
    assert.deepEqual(Object.keys(r.body),['claim']);
    assert.equal(r.propBaseReference,!!reference);
    if(mode==='failed read')throw Error('DOCUMENT_WORKER_LOCAL_TRANSPORT');
    if(mode==='reference stale')throw Error('WORKFLOW_PROP_REFERENCE_CHANGED');
    const asset=basePng({width:48,height:32,alpha:true}),hash=sha(asset);
    const actualRef=reference&&mode!=='reference missing'?{pin:reference,assetId:hash+'.png',
     sha256:hash,base64:(mode==='reference corrupt'?Buffer.from('bad'):asset).toString('base64')}:null;
    return {design:{officialRequestId,taskId:design.id,key:design.key,resultRef:designRef,
     artifact:mode==='wrong design'?{...artifact,appearancePrompt:'tampered'}:artifact,lineage:null},context,...(actualRef?{reference:actualRef}:{}),
     ...(pixel?{source:{taskId:detail.id,resultRef:prior.get(detail)},media:{sha256:sha(detailPng),bytes:detailPng.length,width:1536,height:1024,mediaType:'image/png'},
      canvas:{width:1536,height:1024}}:{})};
   }
   if(pixel&&r.path===propBasePath+'/asset'){
    assert.deepEqual(Object.keys(r.body),['claim','purpose','index']);assert.equal(r.body.purpose,'input');assert.equal(r.body.index,null);
    return mode==='pixel-v2 corrupted'?Buffer.from('corrupt'):detailPng;
   }
   assert.equal(r.path,propBasePath);
   assert.deepEqual(r.body.artifact,{media:{sha256:sha(png),bytes:png.length,width:1536,height:1024,mediaType:'image/png'},base64:png.toString('base64')});
   if(mode==='lost ack once'&&calls.filter(c=>c[0]==='http'&&c[1]==='POST'&&c[2]===propBasePath).length===1)
    throw Error('DOCUMENT_WORKER_LOCAL_TRANSPORT');
   return {ok:true,resultRef:mode==='wrong ack'?'bogus':propRef(base.key,sha(png))};
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
 assert.deepEqual(render[0][1],{projectId:'binari',propId:'12.candle',state:'base',lane:'detail',background:'green-screen.v1',
  intent:f.artifact.appearancePrompt,sources,contextSha256});
 assert.equal(parseImageArgs(['--execute','--db',f.options.dbPath,'--official-request-id',f.options.officialRequestId,'--task-id',
  f.base.id,'--operation',f.base.type]).operation,f.base.type);
 assert.equal((await imageTick(f.options)).state,'idle');
});
for(const mode of ['blocked','wrong design','bad png','alpha','wrong canvas','implicit alpha','wrong ack','revoked'])test(mode+' never falls back and reaches a bounded outcome',async t=>{
 const f=fixture(t,'prop.generate',mode);
 const r=await runPropBaseFromPath(f.options);
 const published=f.calls.some(c=>c[0]==='http'&&c[1]==='POST'&&c[2]===propBasePath);
 switch(mode){
 case 'blocked': assert.equal(r.state,'not_claimable');assert.equal(f.state().status,'pending');break;
 case 'wrong design':
 case 'alpha':
 case 'wrong canvas':
 case 'implicit alpha':
 case 'bad png': assert.equal(r.state,'failed');assert.equal(f.state().status,'failed');break;
 case 'revoked': assert.equal(r.state,'failed');assert.equal(f.state().status,'failed');break;
 case 'wrong ack': assert.equal(r.state,'failed');assert.equal(f.state().status,'failed');break;
 }
 assert.equal(published,mode==='wrong ack');
 if(mode==='wrong design'||mode==='blocked')assert.equal(f.calls.some(c=>c[0]==='render'),false);
});
for(const [mode,failureStage,reasonCode] of [
 ['failed read','input-read','DOCUMENT_WORKER_LOCAL_TRANSPORT'],
 ['wrong design','input-validation','WORKFLOW_PROP_BASE_DESIGN_INVALID'],
 ['cli blocked','model-render','WORKFLOW_IMAGE_CLI_REPORTED_BLOCKED'],
 ['native wrong canvas','png-validation','WORKFLOW_PROP_BASE_PNG_CANVAS'],
 ['bad png','png-validation','WORKFLOW_PROP_BASE_PNG_INVALID'],
 ['private message','model-render','WORKFLOW_PROP_BASE_UNCLASSIFIED'],
 ['wrong ack','stage-write','WORKFLOW_PROP_BASE_PUBLICATION_UNCONFIRMED'],
])test('prop failure logs only bounded stage and reason: '+mode,async t=>{
 const f=fixture(t,'prop.generate',mode);
 const r=await imageTick(f.options);
 assert.equal(r.failureStage,failureStage);assert.equal(r.reasonCode,reasonCode);
 assert.equal(r.state,'failed');
 assert.ok(!JSON.stringify(r).includes('PRIVATE_TEXT'));
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

for(const mode of ['reference valid','reference missing','reference corrupt','reference stale'])
 test(mode+' never substitutes a text-only render for the requested published PNG',async t=>{
  const f=fixture(t,'prop.generate',mode);
  const result=await runPropBaseFromPath(f.options);
  const rendered=f.calls.filter(x=>x[0]==='render');
  if(mode==='reference valid'){
   assert.equal(result.state,'completed');
   assert.equal(rendered.length,1);
   const {assetId,sha256,base64}=rendered[0][1].reference;
   assert.equal(assetId,sha256+'.png');
   assert.deepEqual(Buffer.from(base64,'base64'),basePng({width:48,height:32,alpha:true}));
  }else{
   assert.equal(result.state,'failed');
   assert.equal(result.code,mode==='reference stale'?'WORKFLOW_PROP_BASE_REFERENCE_CHANGED':'WORKFLOW_PROP_BASE_REFERENCE_INVALID');
   assert.equal(rendered.length,0);
  }
 });

test('lost save response marks failed and the normal retry renders and saves again',async t=>{
 const f=fixture(t,'prop.generate','lost ack once');
 const first=await runPropBaseFromPath(f.options);
 assert.equal(first.state,'failed');assert.equal(first.failureStage,'stage-write');
 assert.equal(first.reasonCode,'DOCUMENT_WORKER_LOCAL_TRANSPORT');
 assert.equal(f.state().status,'failed');assert.equal(f.state().result_ref,null);
 assert.equal((await runPropBaseFromPath(f.options)).state,'not_claimable');
 f.db.prepare('UPDATE workflow_tasks SET updated_at=? WHERE task_id=?').run(Date.now()-601000,f.base.id);
 assert.equal((await runPropBaseFromPath(f.options)).state,'completed');
 assert.equal(f.calls.filter(c=>c[0]==='render').length,2);
 assert.equal(f.state().retry_count,1);
});

test('official spirit profile reaches the isolated base image renderer',async t=>{
 for(const type of ['prop.generate','prop.regenerate']){
  const f=fixture(t,type,'spirit');
  assert.equal((await imageTick(f.options)).state,'completed');
  const q=f.calls.find(c=>c[0]==='render')[1];
  assert.equal(q.presentationProfile,'spirit-v1');
  assert.equal(q.background,'green-screen.v1');
  assert.equal(q.intent,f.artifact.appearancePrompt);
 }
});

for(const operation of ['prop.generate','prop.regenerate'])test(operation+' pixel source reads exact RGBA detail and makes a new stage-scoped render',async t=>{
 const f=fixture(t,operation,'pixel-v2');
 assert.equal(nextPropBaseFromPath(f.options.dbPath).operation,f.base.type);
 assert.equal((await runPropBaseFromPath(f.options)).state,'completed');
 const input=f.calls.find(c=>c[0]==='render')[1];
 assert.equal(input.lane,'pixel');
 assert.equal(input.presentationProfile,'spirit-v2');
 assert.equal(sha(Buffer.from(input.reference.base64,'base64')),input.reference.sha256);
 assert.ok(f.state().result_ref.startsWith('prop.pixel-source-png.'));
});
test('pixel source cannot generate with corrupted cinematic reference',async t=>{
 const f=fixture(t,'prop.generate','pixel-v2 corrupted');
 assert.equal((await runPropBaseFromPath(f.options)).state,'failed');
 assert.equal(f.calls.some(c=>c[0]==='render'),false);
});
