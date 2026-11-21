import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,realpathSync,chmodSync,rmSync} from 'node:fs';
import {createServer} from 'node:http';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {PlannerStore} from './vendor/planner-four-doc-v1/planner.mjs';
import {canonical,sha} from './vendor/planner-four-doc-v1/envelope.mjs';
import {validateNPCPlanningUpdate,npcPlanningRoles} from './npc-planning-update-envelope.mjs';
import {documentTick,executeOnce} from './runner.mjs';
import {localPorts,planningUpdatePath,requestLocalJson} from './local-ports.mjs';
import {createPlanningFolderDownloader} from './planning-folders.mjs';
import {planningHeadings} from './planning-markdown.mjs';

const operations=['overview','portrait-art','representative','idle'].map(s=>'npc.plan-'+s),npcId='npc.fixture';
const ids=npcPlanningRoles.map((_,i)=>'category.preset.'+String(i).repeat(32));
const title='정식 기획',sourceFor=op=>'# 기존 H1\n\n'+planningHeadings[op].map(h=>'## '+h+'\n\n보존 내용').join('\n\n');
const generationId='a'.repeat(48),scopes=['binari/concept','binari/npcs/'+npcId+'/planning'];
async function fixture(t,{role,mode}){
 const directory=realpathSync(mkdtempSync(join(tmpdir(),'npc-plan-update-worker-'))),dbPath=join(directory,'workflow.sqlite');
 const store=new PlannerStore(dbPath);chmodSync(dbPath,0o600);
 t.after(()=>{store.close();rmSync(directory,{recursive:true,force:true});});
 const officialRequestId='request.official.'+sha(role+mode), taskId='workflow.task.'+sha(canonical([officialRequestId,'planning_update']));
 const payload={schemaVersion:'ngs.npc-planning-update.v1',projectId:'binari',npcId,role,requirements:'확정된 세부 표현만 수정'};
 const raw=validateNPCPlanningUpdate(payload,'binari'),payloadSha256=sha(raw);
 store.db.prepare("INSERT INTO workflow_runs VALUES(?,?,?,?,?,?,?,'planned',?,NULL)").run(officialRequestId,'binari','npc.planning.update',payload.schemaVersion,raw,payloadSha256,1,1);
 store.db.prepare("INSERT INTO workflow_tasks(task_id,official_request_id,task_key,task_type,worker_type,task_input,updated_at) VALUES(?,?,'planning_update','npc.planning.update','document',?,1)").run(taskId,officialRequestId,canonical({npcId,role}));
 const get=()=>store.db.prepare('SELECT * FROM workflow_tasks WHERE task_id=?').get(taskId);
 const index=npcPlanningRoles.indexOf(role),source=sourceFor(operations[index]),content=source.replace('보존 내용','확정된 수정 내용');
 const calls=[];
 const srv=createServer(async (req,res)=>{
  calls.push([req.method,req.url]);
  let result={enabled:true,schemaVersion:'workflow.npc-planning-update-write.v1'};
  if(req.method==='POST'){
   let body='';for await(const chunk of req)body+=chunk;
   const packet=JSON.parse(body),claim={officialRequestId,taskId,projectId:'binari',npcId,payloadSha256,claimStamp:get().updated_at};
   assert.equal(get().status,'running');assert.deepEqual(packet.claim,claim);
   if(req.url===planningUpdatePath+'/read'){
    assert.deepEqual(packet,{claim});
    result={categoryId:ids[index],role,title,source,sourceSha256:mode==='bad-sha'?'0'.repeat(64):sha(source),generationId};
   }else{
    assert.equal(req.url,planningUpdatePath);
    assert.deepEqual(packet,{claim,artifact:{schemaVersion:'workflow.npc-planning-update-markdown.v1',
     categoryId:ids[index],role,generationId,expectedSourceSha256:sha(source),content}});
    if(mode==='lost-ack'){res.destroy();return;}
    result={ok:true};
   }
  }
  res.writeHead(200,{'Content-Type':'application/json'}).end(JSON.stringify(result));
 });
 await new Promise(ready=>srv.listen(0,'127.0.0.1',ready));t.after(()=>new Promise(done=>srv.close(done)));
 const pds=localPorts({transport:args=>requestLocalJson({...args,port:srv.address().port})});
 const files=Object.fromEntries(ids.flatMap((id,i)=>{
  const text=mode==='bad-snapshot'&&i===index?'changed':sourceFor(operations[i]);
  const meta={id,name:title,planningRole:npcPlanningRoles[i]};
  return mode==='legacy'?[[id+'.json',canonical({...meta,content:text})]]:
   [[id+'.json',canonical({...meta,contentFormat:'markdown.v1',contentSha256:sha(text)})],[id+'/content.md',text]];
 }));
 const withPlanningSnapshot=createPlanningFolderDownloader({tempParent:directory,call:async (tool,args)=>{
  const docs=args.scope===scopes[0]?{'fixture.json':'{}'}:files;
  if(tool==='npc_folders_compare')return {scope:args.scope,groups:[...Map.groupBy(Object.keys(docs),path=>path.includes('/')?path.split('/')[0]:'')].map(([folder,paths])=>({folder,
   files:paths.map(path=>({path,sha256:sha(docs[path]),size:Buffer.byteLength(docs[path])}))}))};
  const bytes=Buffer.from(docs[args.path]);
  return {...args,size:bytes.length,base64:bytes.toString('base64'),nextOffset:null};
 }});
 return {dbPath,officialRequestId,taskId,operation:'npc.planning.update',pds,withPlanningSnapshot,get,calls,content,source,index};
}

for(const [role,mode] of [['overview','legacy'],['portrait.art','markdown'],['portrait.representative','markdown'],['portrait.idle','markdown'],
 ['overview','bad-sha'],['portrait.art','bad-snapshot'],['portrait.idle','lost-ack']])test(role+' '+mode+': exact single document update',async t=>{
 const f=await fixture(t,{role,mode});
 let rendered=0;
 const options={...f,rendererFor:()=>async input=>{
  rendered++;
  assert.equal(input.source,f.source);assert.equal(input.role,role);assert.equal(input.heading,'# 기존 H1');
  const prior=[[],['overviewContent'],['portraitArtContent'],['portraitArtContent','representativeContent']][f.index];
  assert.deepEqual(Object.keys(input).filter(k=>k.endsWith('Content')),prior);
  return f.content;
 }};
 const expected=mode.startsWith('bad-')?'failed':mode==='lost-ack'?'requires_reconciliation':'completed';
 assert.equal((await documentTick(options)).state,expected);
 assert.equal(rendered,expected==='failed'?0:1);
 assert.deepEqual(f.calls,[['GET',planningUpdatePath],['POST',planningUpdatePath+'/read'],
  ...(expected==='failed'?[]:[['POST',planningUpdatePath]])]);
 if(mode==='lost-ack'){
  assert.equal(f.get().status,'running');
  assert.equal((await executeOnce({...f,render:()=>assert.fail('no repeat')})).state,'not_claimable');
 }else assert.equal(f.get().status,expected);
});
