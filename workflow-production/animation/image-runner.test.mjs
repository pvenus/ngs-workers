import test from 'node:test';
import assert from 'node:assert/strict';
import {chmodSync,mkdtempSync,readdirSync,realpathSync,rmSync} from 'node:fs';
import {createServer} from 'node:http';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {imageTick,parseImageArgs,startImageHeartbeat} from './image-runner.mjs';
import {localPorts,portraitPath,requestLocalJson} from './local-ports.mjs';
import {createPlanningFolderDownloader} from './planning-folders.mjs';
import {PlannerStore} from './vendor/planner-f24d23a/planner.mjs';
import {canonical,sha} from './vendor/planner-f24d23a/envelope.mjs';

const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jY2cAAAAASUVORK5CYII=','base64');
const categoryId='category.preset.'+'c'.repeat(32);
const plan='current representative plan only';
const cap={enabled:true,schemaVersion:'workflow.representative-portrait-write.v1'};

for(const mode of ['normal','version-bearing basis','stale plan','invalid PNG','uncertain ACK'])test('representative image resident: '+mode,async t=>{
 const root=realpathSync(mkdtempSync(join(tmpdir(),'image-worker-test-'))),dbPath=join(root,'workflow.sqlite');
 const s=new PlannerStore(dbPath);chmodSync(dbPath,0o600);
 s.db.exec('ALTER TABLE workflow_tasks ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0 CHECK(retry_count BETWEEN 0 AND 3)');
 t.after(()=>{s.close();rmSync(root,{recursive:true,force:true});});
 const payload={schemaVersion:'ngs.npc-one-stop.replace.v1',projectId:'binari',intent:'official figure portrait',
  character:{npcId:'npc.ch1_dajeong',name:'다정',slug:'dajeong',role:'fixture',actId:'act1',chapterId:'ch1'},
  sourceRefs:['story.fixture'],includeCombat:false};
 const payloadSha256=sha(canonical(payload));
 const enroll=(label,registeredAt)=>{
  const officialRequestId='request.official.'+sha(label);
  s.stage({officialRequestId,projectId:'binari',requestType:'npc.workflow',schemaVersion:payload.schemaVersion,
   payload,payloadSha256,status:'registered',registeredAt});
  s.acknowledge(officialRequestId);return officialRequestId;
 };
 // Older blocked job never leads the queue. Another newer ready portrait
 // remains pending when the selected tick completes.
 const old=enroll('blocked-old',1),officialRequestId=enroll('target',2),later=enroll('later',3);
 const get=(rid,key='portrait_representative')=>s.db.prepare('SELECT * FROM workflow_tasks WHERE official_request_id=? AND task_key=?').get(rid,key);
 const taskId=get(officialRequestId).task_id,calls=[],phases=[];
 const basis={categoryId};
 const srv=createServer(async(req,res)=>{
  calls.push([req.method,req.url]);let data=cap;
  if(req.method==='POST'){
   let bytes='';for await(const chunk of req)bytes+=chunk;
   const packet=JSON.parse(bytes),running=get(officialRequestId);
   assert.equal(running.status,'running');
   assert.deepEqual(packet.claim,{officialRequestId,taskId,projectId:'binari',npcId:payload.character.npcId,payloadSha256,claimStamp:running.updated_at});
   if(req.url===portraitPath+'/read'){
    assert.deepEqual(Object.keys(packet),['claim']);data={basis:mode==='version-bearing basis'?{...basis,inputSha256:'a'.repeat(64),generationId:'b'.repeat(48)}:basis,character:payload.character,planningContent:plan};
   }else{
    assert.deepEqual(packet,{claim:packet.claim,basis,artifact:{schemaVersion:'workflow.npc-representative-portrait-artifact.v1',
     taskId,projectId:'binari',npcId:payload.character.npcId,base64:png.toString('base64'),sha256:sha(png)}});
    data={ok:mode!=='uncertain ACK'};
   }
  }
  res.writeHead(200,{'Content-Type':'application/json'}).end(JSON.stringify(data));
 });
 await new Promise(resolve=>srv.listen(0,'127.0.0.1',resolve));t.after(()=>new Promise(resolve=>srv.close(resolve)));
 const pds=localPorts({transport:opts=>{assert.equal(opts.port,8030);return requestLocalJson({...opts,port:srv.address().port});}});
 const concept='binari/concept',planning='binari/npcs/'+payload.character.npcId+'/planning';
 const byScope={
  [concept]:{
   'sections/character/metadata.json':{id:'character',title:'character',summary:'tone',fieldIds:['identity','new.active']},
   'sections/character/fields/identity.json':{id:'identity',label:'identity',value:'approved'},
   'sections/character/fields/new.active.json':{id:'new.active',label:'new.active',value:'all active'},
   'sections/expression/metadata.json':{id:'expression',title:'expression',summary:'style',fieldIds:[]},
   'sections/music/metadata.json':{id:'music',title:'do-not-forward'},
  },
  [planning]:{
   [categoryId+'.json']:{id:categoryId,planningRole:'portrait.representative',content:mode==='stale plan'?'old plan':plan},
   'other.json':{content:'do-not-forward'},
  },
 };
 const received=new Set();
 const withPlanningSnapshot=createPlanningFolderDownloader({tempParent:root,call:async(tool,args)=>{
  const byPath=byScope[args.scope];
  const encode=path=>Buffer.from(JSON.stringify(byPath[path]));
  if(tool==='npc_folders_compare'){
   const groups=new Map();
   for(const path of Object.keys(byPath)){
    const folder=path.includes('/')?path.split('/')[0]:'';
    if(!groups.has(folder))groups.set(folder,[]);
    groups.get(folder).push({path,size:encode(path).length,sha256:sha(encode(path))});
   }
   return {scope:args.scope,groups:[...groups].map(([folder,files])=>({folder,files}))};
  }
  received.add(args.scope+'/'+args.path);
  const buffer=encode(args.path);
  assert.equal(args.sha256,sha(buffer));assert.equal(args.offset,0);
  return {...args,size:buffer.length,base64:buffer.toString('base64'),nextOffset:null};
 }});
 let rendered=0;
 const render=async input=>{
  rendered++;
  assert.equal(input.planningContent,plan);assert.deepEqual(input.character,payload.character);
  assert.equal(input.conceptSources.files.length,4);assert.match(JSON.stringify(input),/all active/);
  assert.doesNotMatch(JSON.stringify(input),/do-not-forward|workflow-planning-folders-/);
  return mode==='invalid PNG'?Buffer.from('bad'):png;
 };
 const opts={mode:'run',dbPath,pds,providerReady:()=>true,render,withPlanningSnapshot,onPhase:p=>phases.push(p)};
 assert.equal((await imageTick({...opts,providerReady:()=>false})).state,'not_ready');
 assert.equal((await imageTick(opts)).state,'idle');
 assert.equal(get(officialRequestId).status,'pending');
 for(const rid of [officialRequestId,later])s.db.prepare("UPDATE workflow_tasks SET status='completed' WHERE official_request_id=? AND task_key='planning_representative'").run(rid);
 assert.equal((await imageTick({...opts,mode:'watch'})).state,'ready');
 assert.equal(get(officialRequestId).status,'pending');
 const unaffected=()=>s.db.prepare('SELECT * FROM workflow_tasks WHERE task_id<>? ORDER BY task_id').all(taskId);
 const before=unaffected();
 const result=await imageTick(opts);
 assert.equal(result.taskId,taskId);assert.equal(result.state,mode==='normal'?'completed':'requires_reconciliation');
 assert.equal(get(officialRequestId).result_ref,null);
 assert.equal(get(officialRequestId).status,mode==='normal'?'completed':'running');
 assert.deepEqual(unaffected(),before);assert.equal(get(old).status,'pending');assert.equal(get(later).status,'pending');
 assert.equal(rendered,['stale plan','version-bearing basis'].includes(mode)?0:1);
 assert.equal(received.size,mode==='version-bearing basis'?0:7);assert.equal(readdirSync(root).some(n=>n.startsWith('workflow-planning-folders-')),false);
 assert.deepEqual(calls.filter(([method])=>method==='POST').map(([,path])=>path),
  (mode==='normal'||mode==='uncertain ACK')?[portraitPath+'/read',portraitPath]:[portraitPath+'/read']);
 assert.deepEqual(phases,mode==='version-bearing basis'?['claimed','reading-current-basis']:mode==='stale plan'?['claimed','reading-current-basis','verifying-source-folders']:
  ['claimed','reading-current-basis','verifying-source-folders','rendering',...(mode==='normal'||mode==='uncertain ACK'?['publishing']:[]),...(mode==='normal'?['completed']:[])]);
 assert.equal((await imageTick({...opts,mode:'execute',officialRequestId,taskId})).state,'not_claimable');
});

test('resident/one-shot image CLI is explicit and cannot select other operations',()=>{
 const db='/private/workflow.sqlite';
 assert.deepEqual(parseImageArgs(['--run','--db',db]),{mode:'run',dbPath:db});
 assert.deepEqual(parseImageArgs(['--watch','--db',db]),{mode:'watch',dbPath:db});
 assert.throws(()=>parseImageArgs(['--run','--db',db,'--operation','npc.portrait-idle-reference']));
 assert.throws(()=>parseImageArgs(['--execute','--db',db]));
});


test('OPS pulse is independent of a held asynchronous render and stops when closed',async t=>{
 const calls=[],pds={async heartbeat(signal,name){calls.push(name);return true;}};
 const heartbeat=startImageHeartbeat({pds,intervalMs:10});
 t.after(heartbeat.close);
 let releaseStarted,releaseRender;
 const started=new Promise(r=>{releaseStarted=r;});
 const done=imageTick({mode:'run',dbPath:'/unused',onHealth:heartbeat.setReady},{
  check:async()=>({ready:true}),select:()=>({taskId:'task',officialRequestId:'official'}),
  execute:()=>{releaseStarted();return new Promise(r=>{releaseRender=r;});},
 });
 await started;
 await new Promise(resolve=>setTimeout(resolve,65));
 assert(calls.length>=2&&calls.every(name=>name==='image-worker'));
 releaseRender({state:'completed'});assert.equal((await done).state,'completed');
 heartbeat.close();const count=calls.length;
 await new Promise(resolve=>setTimeout(resolve,25));assert.equal(calls.length,count);
});
