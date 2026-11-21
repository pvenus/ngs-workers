import assert from 'node:assert/strict';
import {chmodSync,mkdtempSync,realpathSync,rmSync} from 'node:fs';
import {createServer} from 'node:http';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import test from 'node:test';
import {localPorts,conceptCategoryPath,requestLocalJson} from './local-ports.mjs';
import {documentTick,executeOnce,parseArgs} from './runner.mjs';
import {PlannerStore} from './vendor/planner-concept-v1/planner.mjs';
import {canonical,sha} from './vendor/planner-concept-v1/envelope.mjs';

const schema='ngs.concept-category-workflow.v1',operation='concept.category-generate';
const title='한국풍 사운드',body='# '+title+'\n\n## 음색\n\n현악과 대금을 중심으로 잡는다.\n';
function setup(t,mode,requiredSections){
 const root=realpathSync(mkdtempSync(join(tmpdir(),'master-category-worker-'))),dbPath=join(root,'workflow.sqlite');
 const store=new PlannerStore(dbPath);chmodSync(dbPath,0o600);
 t.after(()=>{store.close();rmSync(root,{recursive:true,force:true});});
 const officialRequestId='request.official.'+sha(mode);
 const categoryId=mode==='add'?'category.add.'+'3'.repeat(32):'character';
 const payload={schemaVersion:schema,projectId:'binari',title,categoryId,operation:mode,
  groupId:mode==='add'?'group.sound':null,outline:['migrate','update'].includes(mode)?'':'한국풍 음색의 구성',
  requirements:mode==='migrate'?'':'현악과 대금 포함',...(requiredSections?{requiredSections}:{})};
 const payloadSha256=sha(canonical(payload)),db=store.db;
 store.stage({officialRequestId,requestType:'concept.category.workflow',projectId:'binari',schemaVersion:schema,
  payload,payloadSha256,status:'registered',registeredAt:1});
 store.acknowledge(officialRequestId);
 const get=key=>db.prepare('SELECT * FROM workflow_tasks WHERE task_key=?').get(key);
 return {dbPath,db,payload,taskId:get('category_generate').task_id,officialRequestId,payloadSha256,get};
}

for(const mode of ['add','replace','migrate'])test('exact canonical single category '+mode+' uses matching claim and one Markdown',async t=>{
 const f=setup(t,mode),{payload,taskId,officialRequestId,payloadSha256}=f,calls=[];
 const markdown=mode==='migrate'?'<!-- ngs/category:'+JSON.stringify({id:payload.categoryId,title,fieldIds:['tone']})+
  ' -->\n'+body:body;
 const srv=createServer(async(req,res)=>{
  calls.push([req.method,req.url]);
  let response={enabled:true,schemaVersion:'workflow.concept-category-write.v1'};
  if(req.method==='POST'){
   let bodyText='';for await(const chunk of req)bodyText+=chunk;
   const packet=JSON.parse(bodyText),row=f.get('category_generate');
   assert.equal(row.status,'running');
   const claim={officialRequestId,taskId,projectId:'binari',categoryId:payload.categoryId,payloadSha256,claimStamp:row.updated_at};
   if(req.url===conceptCategoryPath+'/read'){
    assert.equal(mode,'migrate');assert.deepEqual(packet,{claim});
    response={categoryId:payload.categoryId,title,source:markdown};
   }else{
    assert.equal(req.url,conceptCategoryPath);
    assert.deepEqual(packet,{claim,artifact:{schemaVersion:'workflow.concept-category-markdown.v1',
     categoryId:payload.categoryId,title,content:markdown}});
    response={ok:true};
   }
  }
  res.writeHead(200,{'Content-Type':'application/json'}).end(JSON.stringify(response));
 });
 await new Promise(resolve=>srv.listen(0,'127.0.0.1',resolve));t.after(()=>new Promise(resolve=>srv.close(resolve)));
 const pds=localPorts({transport:opts=>{assert.equal(opts.port,8030);return requestLocalJson({...opts,port:srv.address().port});}});
 let models=0;
 const render=async input=>{
  assert.notEqual(mode,'migrate');models++;
  assert.deepEqual(input,{categoryId:payload.categoryId,title,outline:payload.outline,requirements:payload.requirements});
  return body;
 };
 const opts={...f,pds,operation,render,rendererFor:()=>render,
  withPlanningSnapshot:()=>assert.fail('category must not read NPC/master folder snapshot')};
 if(mode==='replace'){
  assert.equal((await executeOnce(opts)).state,'not_claimable');
  assert.equal((await documentTick(opts)).state,'idle');
  f.db.prepare("UPDATE workflow_tasks SET status='completed' WHERE task_key='category_delete'").run();
 }
 const priorDelete=f.get('category_delete');
 const result=await documentTick(opts);
 assert.equal(result.state,'completed');assert.equal(result.resultRef,null);
 assert.equal(result.conversion,mode==='migrate'?'deterministic-markdown':undefined);
 assert.equal(models,mode==='migrate'?0:1);
 assert.deepEqual(f.get('category_delete'),priorDelete);
 assert.equal(f.get('category_generate').status,'completed');
 assert.equal((await documentTick(opts)).state,'idle');
 assert.deepEqual(calls.filter(([verb])=>verb==='POST').map(row=>row[1]),
  mode==='migrate'?[conceptCategoryPath+'/read',conceptCategoryPath]:[conceptCategoryPath]);
});

test('no write for mismatched migrate source or malformed rendered Markdown',async t=>{
 for(const mode of ['migrate','add']){
  const f=setup(t,mode);
  const pds={async capability(){return true;},
   async readConceptCategory(){return {categoryId:'another',title,source:body};},
   publishConceptCategory:()=>assert.fail('zero canonical writes')};
  const result=await executeOnce({...f,pds,operation,render:()=>mode==='migrate'?assert.fail('no model'):'# wrong'});
  assert.equal(result.state,'failed');assert.equal(f.get('category_generate').status,'failed');
 }
});

test('concept category is an explicit supported one-shot and starts no model on remove',t=>{
 const f=setup(t,'migrate');
 assert.equal(parseArgs(['--execute','--db',f.dbPath,'--official-request-id',f.officialRequestId,
  '--task-id',f.taskId,'--operation',operation]).operation,operation);
});

for(const kind of ['authored','structured','wrong digest','wrong title','lost ack'])test('one full source-bound update: '+kind,async t=>{
 const f=setup(t,'update');
 const marker='<!-- ngs/category:'+JSON.stringify({id:f.payload.categoryId,title,fieldIds:['tone']})+' -->\n';
 const source=(kind==='structured'?marker:'')+body,changed=source.replace('현악과 대금을','대금과 피아노를');
 const calls=[];let models=0,writes=0;
 const srv=createServer(async(req,res)=>{
  let input='';for await(const chunk of req)input+=chunk;
  calls.push(req.url);
  const {claim,...others}=JSON.parse(input),current=f.get('category_generate');
  assert.equal(current.status,'running');
  assert.deepEqual(claim,{officialRequestId:f.officialRequestId,taskId:f.taskId,projectId:'binari',
   categoryId:f.payload.categoryId,payloadSha256:f.payloadSha256,claimStamp:current.updated_at});
  let response;
  if(req.url===conceptCategoryPath+'/read'){
   assert.deepEqual(others,{});
   response={categoryId:f.payload.categoryId,title:kind==='wrong title'?'another':title,source,
    sourceSha256:kind==='wrong digest'?'0'.repeat(64):sha(source)};
  }else{
   assert.equal(req.url,conceptCategoryPath);writes++;
   assert.deepEqual(others,{artifact:{schemaVersion:'workflow.concept-category-markdown.v1',categoryId:f.payload.categoryId,
    title,content:changed,expectedSourceSha256:sha(source)}});
   if(kind==='lost ack'){res.destroy();return;}
   response={ok:true};
  }
  res.writeHead(200,{'Content-Type':'application/json'}).end(JSON.stringify(response));
 });
 await new Promise(resolve=>srv.listen(0,'127.0.0.1',resolve));t.after(()=>new Promise(resolve=>srv.close(resolve)));
 const actual=localPorts({transport:opts=>requestLocalJson({...opts,port:srv.address().port})});
 const model=async input=>{
  models++;assert.deepEqual(input,{categoryId:f.payload.categoryId,title,requirements:f.payload.requirements,source});
  return changed;
 };
 const opts={...f,pds:{...actual,capability:async()=>true},operation,render:model,withPlanningSnapshot:()=>assert.fail('no folder snapshot')};
 const expected=['wrong title','wrong digest'].includes(kind)?'failed':kind==='lost ack'?'requires_reconciliation':'completed';
 assert.equal((await executeOnce(opts)).state,expected);
 const invalid=expected==='failed';
 assert.deepEqual(calls,invalid?[conceptCategoryPath+'/read']:[conceptCategoryPath+'/read',conceptCategoryPath]);
 assert.equal(models,invalid?0:1);assert.equal(writes,invalid?0:1);
 assert.equal(f.get('category_delete'),undefined);
 if(kind==='lost ack'){assert.equal((await executeOnce(opts)).state,'not_claimable');assert.equal(writes,1);}
});

for(const invalid of [false,true])test('add section bodies use fixed titles and order; malformed transport no publish: '+invalid,async t=>{
 const sectionTitles=['악기','음색'];
 const f=setup(t,'add',sectionTitles),written=[];
 const ports={async capability(){return true;},readConceptCategory:()=>assert.fail('add does not need prior body'),
  async publishConceptCategory(command){written.push(JSON.parse(command.artifact));return {ok:true};}};
 let renders=0;
 const render=async input=>{
  renders++;assert.deepEqual(input.requiredSections,sectionTitles);
  return invalid?{section_1:'한 절'}:{section_2:'두 번째',section_1:'첫 번째 **강조**\n\n- 항목 1\n- 항목 2\n\n## 작성자가 넣은 제목\n---\n```md\n<div>'};
 };
 const result=await executeOnce({...f,pds:ports,operation,render});
 assert.equal(renders,1);
 assert.equal(result.state,invalid?'failed':'completed');
 assert.equal(written.length,invalid?0:1);
 if(invalid){
  f.db.prepare("UPDATE workflow_tasks SET updated_at=? WHERE task_id=?").run(Date.now()-610000,f.taskId);
  assert.equal((await documentTick({...f,pds:ports,operation,rendererFor:()=>assert.fail('second render')})).state,'idle');
  assert.equal((await executeOnce({...f,pds:ports,operation,render:()=>assert.fail('explicit second render')})).state,'not_claimable');
  assert.equal(f.get('category_generate').retry_count,0);
 }else{
  const content=written[0].content;
  assert.deepEqual(content.split('\n').filter(line=>line.startsWith('## ')),['## 악기','## 음색']);
  assert.match(content,/첫 번째/);assert.match(content,/두 번째/);
  assert.ok(content.includes('\\## 작성자가 넣은 제목'));
  assert.ok(content.includes('첫 번째 **강조**\n\n- 항목 1\n- 항목 2'));
  assert.ok(content.includes('\\---\n\\```md\n\\<div>'));
 }
});

test('historical add without structured sections retains the existing aged retry behavior',async t=>{
 const f=setup(t,'add');
 const ports={async capability(){return true;},readConceptCategory:()=>assert.fail('add need not read'),
  async publishConceptCategory(){return {ok:true};}};
 const options={...f,pds:ports,operation};
 assert.equal((await executeOnce({...options,render:()=>''})).state,'failed');
 f.db.prepare("UPDATE workflow_tasks SET updated_at=? WHERE task_id=?").run(Date.now()-610000,f.taskId);
 assert.equal((await documentTick({...options,rendererFor:()=>()=>body})).state,'completed');
 assert.equal(f.get('category_generate').retry_count,1);
});
