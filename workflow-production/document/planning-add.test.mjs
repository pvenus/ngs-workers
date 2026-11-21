import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,realpathSync,chmodSync,rmSync} from 'node:fs';
import {createServer} from 'node:http';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {PlannerStore} from './vendor/planner-four-doc-v1/planner.mjs';
import {canonical,sha} from './vendor/planner-four-doc-v1/envelope.mjs';
import {validateNPCPlanningAdd,npcPlanningAddSchema} from './npc-planning-add-envelope.mjs';
import {documentTick,executeOnce} from './runner.mjs';
import {localPorts,planningAddPath,requestLocalJson} from './local-ports.mjs';
import {createPlanningFolderDownloader} from './planning-folders.mjs';

const npcId='npc.ch1_dajeong',categoryId='category.add.'+'b'.repeat(32),title='인물 습관',generationId='a'.repeat(48);
const scopes=['binari/concept','binari/npcs/'+npcId+'/planning'];
const md='# 인물 습관\n\n## 태도\n\n**차분히** 대한다.\n\n## 관찰\n\n- 주변을 살핀다.\n';
async function fixture(t,mode){
 const root=realpathSync(mkdtempSync(join(tmpdir(),'npc-planning-add-test-'))),dbPath=join(root,'workflow.sqlite');
 const store=new PlannerStore(dbPath);chmodSync(dbPath,0o600);
 t.after(()=>{store.close();rmSync(root,{recursive:true,force:true});});
 const officialRequestId='request.official.'+sha(mode),taskId='workflow.task.'+sha(canonical([officialRequestId,'planning_add']));
 const payload={schemaVersion:npcPlanningAddSchema,projectId:'binari',npcId,categoryId,title,outline:'단정한 인물의 생활 습관',
  requirements:'확정된 내용은 유지',requiredSections:['태도','관찰']};
 const raw=validateNPCPlanningAdd(payload,'binari'),payloadSha256=sha(raw);
 store.db.prepare("INSERT INTO workflow_runs VALUES(?,?,?,?,?,?,?,'planned',?,NULL)").run(officialRequestId,'binari','npc.planning.add',payload.schemaVersion,raw,payloadSha256,1,1);
 store.db.prepare("INSERT INTO workflow_tasks(task_id,official_request_id,task_key,task_type,worker_type,task_input,updated_at) VALUES(?,?,'planning_add','npc.planning.add','document',?,1)").run(taskId,officialRequestId,canonical({npcId,categoryId}));
 const get=()=>store.db.prepare('SELECT * FROM workflow_tasks WHERE task_id=?').get(taskId);
 const calls=[];
 const srv=createServer(async(req,res)=>{
  calls.push([req.method,req.url]);
  let result={enabled:true,schemaVersion:'workflow.npc-planning-add-write.v1'};
  if(req.method==='POST'){
   let body='';for await(const chunk of req)body+=chunk;
   const packet=JSON.parse(body);
   assert.equal(get().status,'running');
   const claim={officialRequestId,taskId,projectId:'binari',npcId,payloadSha256,claimStamp:get().updated_at};
   if(req.url===planningAddPath+'/read'){
    assert.deepEqual(packet,{claim});
    result={categoryId:mode==='wrong-basis'?'category.add.'+'c'.repeat(32):categoryId,title,generationId};
   }else{
    assert.equal(req.url,planningAddPath);
    assert.deepEqual(packet,{claim,artifact:{schemaVersion:'workflow.npc-planning-add-markdown.v1',categoryId,title,generationId,content:md}});
    if(mode==='lost-ack'){res.destroy();return;}
    result={ok:true};
   }
  }
  res.writeHead(200,{'Content-Type':'application/json'}).end(JSON.stringify(result));
 });
 await new Promise(ready=>srv.listen(0,'127.0.0.1',ready));t.after(()=>new Promise(done=>srv.close(done)));
 const pds=localPorts({transport:opts=>requestLocalJson({...opts,port:srv.address().port})});
 const overviewId='category.preset.'+'a'.repeat(32),overview='# 현재 다정 개요';
 const plan=mode==='empty-planning'?{}:{
  [overviewId+'.json']:canonical({id:overviewId,planningRole:'overview',contentFormat:'markdown.v1',contentSha256:sha(overview)}),
  [overviewId+'/content.md']:overview,
  'unrelated.json':'{"text":"OTHER CONTENT never prompt"}',
  ...(mode==='occupied'?{[categoryId+'.json']:'{}'}:{})};
 const readScopes=[];
 const withPlanningSnapshot=createPlanningFolderDownloader({tempParent:root,call:async(tool,args)=>{
  assert(scopes.includes(args.scope));
  readScopes.push(args.scope);
  const files=args.scope===scopes[0]?{'fixture.json':'{}'}:plan;
  if(tool==='npc_folders_compare')return {scope:args.scope,groups:[...Map.groupBy(Object.keys(files),path=>path.includes('/')?path.split('/')[0]:'')].map(([folder,paths])=>({folder,
   files:paths.map(path=>({path,sha256:sha(files[path]),size:Buffer.byteLength(files[path])}))}))};
  assert.equal(tool,'npc_folder_file_chunk');
  const bytes=Buffer.from(files[args.path]);
  return {...args,size:bytes.length,base64:bytes.toString('base64'),nextOffset:null};
 }});
 return {dbPath,officialRequestId,taskId,operation:'npc.planning.add',pds,withPlanningSnapshot,get,calls,readScopes,overview,payload};
}

for(const mode of ['good','empty-planning','occupied','wrong-basis','lost-ack','bad-format'])test('exact isolated NPC planning add '+mode,async t=>{
 const f=await fixture(t,mode);
 let rendered=0;
 const options={...f,rendererFor:()=>async(input)=>{
  rendered++;
  assert.equal(input.npcId,npcId);assert.equal(input.categoryId,categoryId);
  assert.deepEqual(input.requiredSections,f.payload.requiredSections);
  assert.equal(input.overviewContent,mode==='empty-planning'?null:f.overview);
  return mode==='bad-format'?{section_1:'한 절뿐'}:{section_2:'- 주변을 살핀다.',section_1:'**차분히** 대한다.'};
 }};
 const neverRender=['occupied','wrong-basis'].includes(mode);
 const neverPublish=neverRender||mode==='bad-format';
 const expected=neverPublish?'failed':mode==='lost-ack'?'requires_reconciliation':'completed';
 assert.equal((await documentTick(options)).state,expected);
 assert.equal(rendered,neverRender?0:1);
 assert.deepEqual(f.calls,[['GET',planningAddPath],['POST',planningAddPath+'/read'],...(!neverPublish?[['POST',planningAddPath]]:[])]);
 if(mode==='wrong-basis')assert.equal(f.readScopes.length,0);
 if(mode==='lost-ack')assert.equal((await executeOnce({...f,render:()=>assert.fail('no replay')})).state,'not_claimable');
});
