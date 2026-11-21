import assert from 'node:assert/strict';
import {chmodSync,mkdtempSync,realpathSync,rmSync} from 'node:fs';
import {createServer} from 'node:http';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import test from 'node:test';
import {localPorts,conceptCategoryPath,requestLocalJson} from './local-ports.mjs';
import {pollOnce,executeOnce,parseArgs} from './runner.mjs';
import {PlannerStore} from './vendor/planner-concept-v1/planner.mjs';
import {canonical,sha} from './vendor/planner-concept-v1/envelope.mjs';

const schema='ngs.concept-category-workflow.v1',operation='concept.category-delete';
function setup(t,mode){
 const root=realpathSync(mkdtempSync(join(tmpdir(),'master-category-file-'))),dbPath=join(root,'workflow.sqlite');
 const store=new PlannerStore(dbPath);chmodSync(dbPath,0o600);
 t.after(()=>{store.close();rmSync(root,{recursive:true,force:true});});
 const officialRequestId='request.official.'+sha(mode),categoryId='category.add.'+'c'.repeat(24);
 const payload={schemaVersion:schema,projectId:'binari',title:'테스트 카테고리',categoryId,operation:mode,
  groupId:null,outline:mode==='replace'?'새 개요':'',requirements:mode==='replace'?'새 조건':''};
 const payloadSha256=sha(canonical(payload)),db=store.db;
 store.stage({officialRequestId,requestType:'concept.category.workflow',projectId:'binari',schemaVersion:schema,
  payload,payloadSha256,status:'registered',registeredAt:1});store.acknowledge(officialRequestId);
 const get=key=>db.prepare('SELECT * FROM workflow_tasks WHERE task_key=?').get(key);
 return {dbPath,db,payload,taskId:get('category_delete')?.task_id,officialRequestId,payloadSha256,get};
}

for(const [mode,fault] of [['replace',null],['remove',null],['remove','denial'],['remove','untrusted-denial'],['remove','unknown'],['remove','negative-ack']])
test('Master Concept file worker exact '+mode+' across '+(fault??'success'),async t=>{
 const f=setup(t,mode),{payload,taskId,officialRequestId,payloadSha256}=f,calls=[];
 const srv=createServer(async(req,res)=>{
  calls.push([req.method,req.url]);assert.equal(req.url,conceptCategoryPath);
  let response={enabled:true,schemaVersion:'workflow.concept-category-write.v1'},status=200;
  if(req.method==='POST'){
   let raw='';for await(const chunk of req)raw+=chunk;
   const row=f.get('category_delete');
   assert.equal(row.status,'running');
   assert.deepEqual(JSON.parse(raw),{claim:{officialRequestId,taskId,projectId:'binari',categoryId:payload.categoryId,payloadSha256,claimStamp:row.updated_at}});
   if(fault==='unknown'){res.destroy();return;}
   if(fault==='denial'||fault==='untrusted-denial'){
    response={error:fault==='denial'?'CONCEPT_REQUIRED_CATEGORY_REMOVAL_DENIED':'UNRECOGNIZED_DENIAL'};status=403;
   }else response={ok:fault!=='negative-ack'};
  }
  res.writeHead(status,{'Content-Type':'application/json'}).end(JSON.stringify(response));
 });
 await new Promise(resolve=>srv.listen(0,'127.0.0.1',resolve));t.after(()=>new Promise(resolve=>srv.close(resolve)));
 const pds=localPorts({transport:opts=>{assert.equal(opts.port,8030);return requestLocalJson({...opts,port:srv.address().port});}});
 const doc=f.get('category_generate'),run=f.db.prepare('SELECT * FROM workflow_runs').get();
 const result=await pollOnce({...f,pds});
 const expected=!fault?'completed':fault==='denial'?'failed':'requires_reconciliation';
 assert.equal(result.state,expected);
 if(!fault)assert.equal(result.resultRef,null);
 assert.equal(f.get('category_delete').status,expected==='requires_reconciliation'?'running':expected);
 assert.equal(f.get('category_delete').result_ref,null);
 assert.deepEqual(f.get('category_generate'),doc);
 assert.deepEqual(f.db.prepare('SELECT * FROM workflow_runs').get(),run);
 assert.equal((await pollOnce({...f,pds})).state,'idle');
 assert.equal(calls.filter(([verb])=>verb==='POST').length,1);
});

test('add/migrate never selected for file delete and explicit wrong binding sends no POST',async t=>{
 const f=setup(t,'migrate');
 const pds={capability:async()=>true,deleteTargets:()=>assert.fail('must not delete')};
 assert.equal((await pollOnce({...f,pds})).state,'idle');
 assert.equal((await executeOnce({...f,pds,taskId:f.get('category_generate').task_id,operation})).state,'not_claimable');
 assert.equal(f.get('category_generate').status,'pending');
 const g=setup(t,'replace');
 g.db.prepare("UPDATE workflow_tasks SET task_input=? WHERE task_key='category_delete'").run(canonical({categoryId:'unrelated'}));
 assert.equal((await pollOnce({...g,pds})).state,'blocked');
 assert.equal(g.get('category_delete').status,'pending');
 assert.equal(parseArgs(['--execute','--db',g.dbPath,'--official-request-id',g.officialRequestId,'--task-id',g.taskId,'--operation',operation]).operation,operation);
});
