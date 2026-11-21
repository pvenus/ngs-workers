import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,realpathSync,rmSync,chmodSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {PlannerStore,buildPlan} from './vendor/planner-four-doc-v1/planner.mjs';
import {canonical,sha} from './vendor/planner-four-doc-v1/envelope.mjs';
import {documentTick,executeOnce} from './runner.mjs';
import {localPorts} from './local-ports.mjs';
import {createPlanningFolderDownloader} from './planning-folders.mjs';
import {planningHeadings,validatePlanningMarkdown} from './planning-markdown.mjs';

const roles=['overview','portrait.art','portrait.representative','portrait.idle'];
const categoryNames=['overview','portrait-art','portrait.representative','portrait.idle'];
const keys=['planning_overview','planning_portrait_art','planning_representative','planning_idle'];
const ops=['overview','portrait-art','representative','idle'].map(k=>'npc.plan-'+k);
const upstream=[[],[['overviewContent',0]],[['portraitArtContent',1]],[['portraitArtContent',1],['representativeContent',2]]];
const scopes=['binari/concept','binari/npcs/npc.fixture/planning'];
const doc=(operation)=>'# 다정 기획\n\n'+planningHeadings[operation].map((h,j)=>'## '+h+'\n\n- 한 명의 인물, 긴 치마와 챕터 상태 '+j).join('\n\n');

function fixture(t,{tamper=false}={}){
 const home=realpathSync(mkdtempSync(join(tmpdir(),'four-doc-workflow-'))),dbPath=join(home,'workflow.sqlite');
 const s=new PlannerStore(dbPath);chmodSync(dbPath,0o600);
 t.after(()=>{s.close();rmSync(home,{recursive:true,force:true});});
 const payload={schemaVersion:'ngs.npc-one-stop.replace.v1',projectId:'binari',
  intent:'비전투 마을 주민 다정, 긴 치마 유지',character:{npcId:'npc.fixture',name:'다정',slug:'fixture',
   role:'마을 주민',actId:'act01',chapterId:'chapter01'},sourceRefs:['t.fixture'],includeCombat:false};
 const officialRequestId='request.official.'+sha('four-doc-fixture'),wire={officialRequestId,projectId:'binari',
  schemaVersion:payload.schemaVersion,payload,payloadSha256:sha(canonical(payload)),requestType:'npc.workflow',
  registeredAt:1000,status:'registered'};
 const graph=buildPlan(wire);s.stage(wire);s.acknowledge(officialRequestId);
 const row=k=>s.db.prepare('SELECT * FROM workflow_tasks WHERE task_key=?').get(k);
 s.db.prepare("UPDATE workflow_tasks SET status='completed' WHERE task_key IN ('delete','preset')").run();
 const categoryIds=categoryNames.map(k=>'category.preset.'+sha(canonical(['npc.standard','3',row('preset').task_id,k])).slice(0,32));
 const saved=new Map(),calls=[],snapshots=[];
 const pds=localPorts({transport:async ({port,path,method,body})=>{
  assert.equal(port,8030);
  const i=ops.findIndex(op=>path.startsWith('/v1/npcs/ownership/workflow-plan-'+op.slice('npc.plan-'.length))&&
     /^([^?]+)(?:\?|$)/.exec(path)[1].replace(/\/read$/,'').endsWith('/workflow-plan-'+op.slice('npc.plan-'.length)));
  assert(i>=0,path);const op=ops[i];calls.push([op,method,path.endsWith('/read')]);
  if(method==='GET')return {enabled:true,schemaVersion:'workflow.'+op.slice('npc.plan-'.length)+'-plan-write.v1'};
  const task=row(keys[i]);const claim=body.claim;
  assert.equal(task.status,'running');
  assert.deepEqual(claim,{officialRequestId,taskId:task.task_id,projectId:'binari',npcId:payload.character.npcId,
   payloadSha256:wire.payloadSha256,claimStamp:task.updated_at});
  const basis={categoryId:categoryIds[i]};
  if(path.endsWith('/read')){
   assert.equal(body.inputMode,'folder-snapshot');
   return {basis,character:payload.character,presetContext:canonical({creationBasis:{},planningPreset:{},category:{
    id:basis.categoryId,planningRole:roles[i],content:''},chapters:[],status:'active'}),
    ...Object.fromEntries(upstream[i].map(([k,j])=>[k,saved.get(j)]))};
  }
  assert.deepEqual(body.basis,basis);
  assert.deepEqual(body.artifact,{schemaVersion:'workflow.npc-'+op.slice('npc.plan-'.length)+'-plan-artifact.v1',
    taskId:task.task_id,projectId:payload.projectId,npcId:payload.character.npcId,categoryId:basis.categoryId,content:doc(op)});
  saved.set(i,body.artifact.content);return {ok:true};
 }});
 const withPlanningSnapshot=createPlanningFolderDownloader({tempParent:home,call:async (tool,args)=>{
  snapshots.push([tool,args.scope]);
  const rows=args.scope===scopes[0]?{'master.json':'{}'}:
   Object.fromEntries(categoryIds.map((id,i)=>[id+'.json',canonical({id,planningRole:roles[i],
    content:tamper&&i===0&&saved.has(i)?'tampered':saved.get(i)??''})]));
  if(tool==='npc_folders_compare')return {scope:args.scope,groups:[{folder:'',files:Object.entries(rows).map(([path,text])=>
    ({path,sha256:sha(text),size:Buffer.byteLength(text)}))}]};
  const data=Buffer.from(rows[args.path]);return {...args,size:data.length,base64:data.toString('base64'),nextOffset:null};
 }});
 return {s,dbPath,officialRequestId,graph,row,saved,calls,snapshots,pds,withPlanningSnapshot};
}

test('the resident document worker walks all four new typed docs with only exact approved upstream',async t=>{
 const f=fixture(t),calls=[];
 const options={...f,rendererFor:op=>async(input,{fourDoc})=>{
  const i=ops.indexOf(op);assert.equal(fourDoc,true);calls.push(op);
  for(const key of ['overviewContent','portraitArtContent','representativeContent']){
   const found=upstream[i].find(([k])=>k===key);
   if(found)assert.equal(input[key],f.saved.get(found[1]));else assert(!Object.hasOwn(input,key));
  }
  assert.equal(input.character.npcId,'npc.fixture');
  return doc(op);
 }};
 for(let i=0;i<4;i++){
  assert.equal((await documentTick(options)).state,'completed');assert.equal(f.row(keys[i]).status,'completed');
  assert.equal(f.row(keys[i]).retry_count,0);assert.equal(f.row(keys[i]).result_ref,null);
  assert.deepEqual(f.calls.slice(i*3,i*3+3),[[ops[i],'GET',false],[ops[i],'POST',true],[ops[i],'POST',false]]);
 }
 assert.deepEqual(calls,ops);assert.equal(f.saved.size,4);
 assert.deepEqual(await documentTick(options),{state:'idle'});
 assert.equal(f.row('portrait_representative').status,'pending');
 assert.equal(f.row('portrait_idle_reference').status,'pending');
});

test('a malformed Markdown result fails before publication and does not permit art',async t=>{
 const f=fixture(t),settings={...f,operation:ops[0],taskId:f.row(keys[0]).task_id,
  render:async()=>doc(ops[0]).replace('## 현재 상태','## 틀린 제목')};
 const result=await executeOnce(settings);
 assert.equal(result.state,'failed');assert.equal(f.row(keys[0]).status,'failed');
 assert.deepEqual(f.calls,[[ops[0],'GET',false],[ops[0],'POST',true]]);
 assert.equal(f.saved.size,0);
 assert.equal((await documentTick({...f,operation:ops[1],rendererFor:()=>assert.fail('no downstream renderer')})).state,'idle');
});

test('a differing downloaded upstream is rejected before art render and publish',async t=>{
 const f=fixture(t,{tamper:true});
 assert.equal((await documentTick({...f,operation:ops[0],rendererFor:op=>async()=>doc(op)})).state,'completed');
 assert.equal((await documentTick({...f,operation:ops[1],rendererFor:()=>async()=>assert.fail('different canonical body')})).state,'failed');
 assert.equal(f.saved.size,1);assert.equal(f.row(keys[2]).status,'pending');
});

test('strict Markdown contract returns clean compact first blocks and rejects fake, empty, or referential blocks',()=>{
 for(const operation of ops)assert.deepEqual(Object.keys(validatePlanningMarkdown(doc(operation),operation)),planningHeadings[operation]);
 const art=doc(ops[1]),first=planningHeadings[ops[1]][0];
 for(const bad of [
  art.replace('## '+first,'### '+first),
  art.replace('## '+first,'```markdown\n## '+first),
  art.replace('## '+first,'## '+first+'\n\n## '+first),
  art.replace('- 한 명의 인물, 긴 치마와 챕터 상태 0',''),
  art.replace('- 한 명의 인물, 긴 치마와 챕터 상태 0','a'.repeat(64)),
  art.replace('- 한 명의 인물, 긴 치마와 챕터 상태 0','길'.repeat(1001)),
 ])assert.throws(()=>validatePlanningMarkdown(bad,ops[1]),/WORKFLOW_PLAN_MARKDOWN_CONTRACT/);
});
