import assert from 'node:assert/strict';
import test from 'node:test';
import {mkdtempSync,realpathSync,rmSync,writeFileSync} from 'node:fs';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {DatabaseSync} from 'node:sqlite';
import {canonical,sha} from './vendor/planner-f24d23a/envelope.mjs';
import {buildPropGeneratePlan,propKeys,propRef} from './vendor/prop-composite/prop-generate-plan.mjs';
import {propPackageReceipt} from './vendor/prop-composite/prop-package-contract.mjs';
import {propStagePorts,stagePath,packagePath} from './prop-stage-ports.mjs';
import {nextPropPackageFromPath,runPropPackageFromPath} from './prop-package-worker.mjs';
import {nextPropDerivationFromPath,runPropDerivationFromPath} from './prop-derivation-worker.mjs';

const officialRequestId='request.official.'+'3'.repeat(64);
const dig=i=>String(i).repeat(64);
function make(t,{regenerate=false,motion='still',spirit=false,scale=false}={}){
 const root=realpathSync(mkdtempSync(join(tmpdir(),'prop-publish-')));
 const dbPath=join(root,'plan.sqlite');writeFileSync(dbPath,'',{mode:0o600});
 const db=new DatabaseSync(dbPath);t.after(()=>{db.close();rmSync(root,{recursive:true,force:true});});
 db.exec(`CREATE TABLE workflow_runs(official_request_id,project_id,request_type,schema_version,payload,payload_sha256,registered_at,state,updated_at,reason);
  CREATE TABLE workflow_tasks(task_id,official_request_id,task_key,task_type,worker_type,task_input,result_ref,updated_at,depends_on_task_id,status,retry_count,depends_on_task_id_2,depends_on_task_id_3);`);
 const type=scale?'prop.scale.generate':regenerate?'prop.regenerate':'prop.generate';
 const payload=scale?{schemaVersion:'ngs.prop-scale-generate.v1',projectId:'binari',propId:'test-bell',source:{revision:4,sha256:dig(9)},scalePercents:[5,100]}:{schemaVersion:regenerate?'ngs.prop-regenerate.v1':'ngs.prop-generate.v1',projectId:'binari',
   propId:'test-bell',designContextSha256:dig(1),intent:'독립 프랍',motion,...(spirit?{presentationProfile:'spirit-v1'}:{}),...(regenerate?{source:{revision:4,sha256:dig(9)}}:{})};
 const source=canonical(payload),requestSha256=sha(source);
 db.prepare("INSERT INTO workflow_runs VALUES(?,?,?,?,?,?,1,'planned',1,NULL)")
  .run(officialRequestId,'binari',type,payload.schemaVersion,source,requestSha256);
 const plan=buildPropGeneratePlan({officialRequestId,requestType:type,payload});
 const by=Object.fromEntries(plan.map(x=>[x.key,x]));
 const hashes={[propKeys.design]:dig(1),[propKeys.base]:dig(2),[propKeys.detail]:dig(3),[propKeys.pixel]:dig(4),[propKeys.spirit]:dig(5)};
 for(const x of plan){
  const ref=hashes[x.key]?propRef(x.key,hashes[x.key]):null;
  db.prepare('INSERT INTO workflow_tasks VALUES (?,?,?,?,?,?,?,2,?,?,0,?,?)')
   .run(x.id,officialRequestId,x.key,x.type,x.workerType,canonical(x.input),ref,x.dependsOn,ref?'completed':'pending',x.additionalDependencies?.[0]??null,x.additionalDependencies?.[1]??null);
 }
 const m=(hash,width)=>({assetId:hash+'.png',sha256:hash,width,height:width,bytes:80,mediaType:'image/png',frameDurationsMs:[]});
 const manifest={schemaVersion:'ngs.prop-package.v1',projectId:'binari',propId:payload.propId,
  revision:scale||regenerate?5:1,parentSha256:scale||regenerate?dig(9):null,officialRequestId,requestSha256,
  designContextSha256:payload.designContextSha256??dig(1),title:'종',media:{detail:m(dig(3),512),pixel:m(dig(4),spirit?102:256),...(spirit?{spirit:m(dig(5),102)}:{})},
  interaction:{pixelOpen:{action:'open',x:1,y:2,width:30,height:40},detailTrigger:null},animations:[],...(scale?{scaleVariants:[{scalePercent:5,pixel:m(dig(6),26),spirit:m(dig(7),26)},
   {scalePercent:100,pixel:m(dig(8),512),spirit:m(dig(3),512)}]}:{})};
 const taskId=by[propKeys.publish].id;
 const state=()=>db.prepare('SELECT status,result_ref,retry_count FROM workflow_tasks WHERE task_id=?').get(taskId);
 const run=(pds)=>runPropPackageFromPath({dbPath,taskId,officialRequestId,pds});
 return {db,dbPath,by,taskId,payload,manifest,state,run};
}
test('create and regenerate: same still worker publishes exact immutable manifest and completes only on verified ACK',async t=>{
 for(const regenerate of [false,true]){
  const f=make(t,{regenerate});
  const refs=await propPackageReceipt(f.manifest);
  assert.deepEqual(nextPropPackageFromPath(f.dbPath),{taskId:f.taskId,officialRequestId});
  let posted=0,seen;
  const pds={preparePropPublication(claim){seen=claim;assert.equal(f.state().status,'running');return {manifest:f.manifest};},
   publishPropPackage(claim,manifest){posted++;assert.deepEqual(claim,seen);assert.deepEqual(manifest,f.manifest);return {ok:true,resultRef:refs};}};
  assert.deepEqual(await f.run(pds),{state:'completed',taskId:f.taskId,resultRef:refs});
  assert.equal(f.state().result_ref,refs);assert.equal(nextPropPackageFromPath(f.dbPath),null);
  assert.equal((await f.run(pds)).state,'not_claimable');assert.equal(posted,1);
 }
});
test('partial or changed receipt stops before publish; uncertain publish retains exact running task',async t=>{
 const f=make(t,{regenerate:true});
 let posted=0;
 const pds={preparePropPublication(){return {manifest:{...f.manifest,parentSha256:dig(8)}};},
  publishPropPackage(){posted++;throw Error('ack lost');}};
 const row=f.by[propKeys.detail].id;
 f.db.prepare("UPDATE workflow_tasks SET status='pending' WHERE task_id=?").run(row);
 assert.equal(nextPropPackageFromPath(f.dbPath),null);assert.equal((await f.run(pds)).state,'not_claimable');
 f.db.prepare("UPDATE workflow_tasks SET status='completed' WHERE task_id=?").run(row);
 assert.equal((await f.run(pds)).state,'failed');assert.equal(posted,0);assert.equal(f.state().retry_count,0);
 f.db.prepare('UPDATE workflow_tasks SET updated_at=? WHERE task_id=?').run(Date.now()-610000,f.taskId);
 pds.preparePropPublication=()=>({manifest:f.manifest});
 assert.equal((await f.run(pds)).state,'requires_reconciliation');assert.equal(posted,1);
 assert.equal(f.state().status,'running');assert.equal(f.state().retry_count,1);assert.equal(f.state().result_ref,null);
});
test('motion remains disabled for resident derive and publish even if a motion row is inserted',async t=>{
 const f=make(t,{motion:'subtle'});
 const detail=f.by[propKeys.detail].id;
 f.db.prepare("UPDATE workflow_tasks SET status='pending',result_ref=NULL WHERE task_id=?").run(detail);
 assert.equal(nextPropPackageFromPath(f.dbPath),null);
 assert.equal(nextPropDerivationFromPath(f.dbPath,{stillOnly:true}),null);
 const pds=Object.fromEntries(['readPropDerivation','fetchPropDerivationAsset','stagePropDerivation'].map(k=>[k,()=>assert.fail(k)]));
 assert.equal((await runPropDerivationFromPath({dbPath:f.dbPath,officialRequestId,taskId:detail,pds,stillOnly:true,render(){assert.fail();}})).state,'not_claimable');
});
test('strict transport capability; exact binary/stdout upload wire and mutation drain',async()=>{
 const calls=[];
 let modes=['still'];
 const png=Buffer.from([1,2,3]);
 const pds=propStagePorts({transport(path,opts){
  calls.push({path,opts});
  if(path.endsWith('/asset'))return png;
  return Buffer.from(JSON.stringify(path===stagePath&&!opts.body?{enabled:true,schemaVersion:'workflow.prop-stage.v1'}:
   path===packagePath&&!opts.body?{enabled:true,schemaVersion:'workflow.prop-package-write.v1',modes}:{ok:true}));
 }});
 assert.equal(await pds.ready(),true);modes=['still','subtle'];assert.equal(await pds.ready(),false);
 const claim={taskId:'exact'},signal=new AbortController().signal;
 const expected={mediaType:'image/png'};
 assert.equal(await pds.fetchPropDerivationAsset({claim},expected,{signal}),png);
 assert.deepEqual(calls.at(-1).opts.body,{claim,purpose:'input',index:null});
 await pds.stagePropDerivation({claim},{media:expected,bytes:png},{signal});
 assert.deepEqual(calls.at(-1).opts.body,{claim,artifact:{media:expected,base64:'AQID'}});
 assert.equal(calls.at(-1).opts.signal,undefined);
 await pds.preparePropPublication(claim,{signal});assert.equal(calls.at(-1).path,packagePath+'/prepare');
 await pds.publishPropPackage(claim,{one:1});assert.deepEqual(calls.at(-1).opts.body,{claim,manifest:{one:1}});
});

test('Spirit package readiness and publish bind third static receipt at one-fifth canvas',async t=>{
 for(const regenerate of [false,true]){
  const f=make(t,{regenerate,spirit:true});
  const row=f.by[propKeys.spirit].id;
  f.db.prepare("UPDATE workflow_tasks SET status='pending' WHERE task_id=?").run(row);
  assert.equal(nextPropPackageFromPath(f.dbPath),null);
  f.db.prepare("UPDATE workflow_tasks SET status='completed' WHERE task_id=?").run(row);
  assert.deepEqual(nextPropPackageFromPath(f.dbPath),{taskId:f.taskId,officialRequestId});
  const resultRef=await propPackageReceipt(f.manifest);
  let posted=0;
  const pds={preparePropPublication:()=>({manifest:f.manifest}),
   publishPropPackage(_claim,m){posted++;assert.deepEqual(m,f.manifest);return {ok:true,resultRef};}};
  assert.equal((await f.run(pds)).state,'completed');
  assert.equal(posted,1);
 }
});

test('scale append has its own single task and accepts only matching PDS publication ACK',async t=>{
 const f=make(t,{scale:true});
 assert.deepEqual(Object.keys(f.by),[propKeys.publish]);
 assert.deepEqual(nextPropPackageFromPath(f.dbPath),{taskId:f.taskId,officialRequestId});
 const ref=await propPackageReceipt(f.manifest);
 const result=await f.run({
  preparePropPublication(claim){assert.equal(claim.taskId,f.taskId);return {manifest:f.manifest};},
  publishPropPackage(claim,manifest){assert.equal(claim.taskId,f.taskId);assert.deepEqual(manifest,f.manifest);return {ok:true,resultRef:ref};},
 });
 assert.equal(result.state,'completed');
 assert.equal(f.state().result_ref,ref);
});
