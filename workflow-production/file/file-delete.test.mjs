import test from 'node:test';
import assert from 'node:assert/strict';
import {deleteCommandFromPlannedTask,exactDeleteTarget} from './file-delete.mjs';
import {localPorts,conceptCategoryPath,deletePath} from './local-ports.mjs';

const conceptPlan={type:'concept.category-delete',key:'category_delete',workerType:'file',input:{categoryId:'category.add.494e3549a0f262e04c571ad4'}};
const npcPlan={type:'npc.delete-folder',key:'delete',workerType:'file',input:{npcId:'npc.ch1_dajeong',scope:'exact-npc-folder',preserveRequestHistory:true,noOldDataReuse:true}};
const binding={officialRequestId:'request.official.'+'f'.repeat(64),taskId:'workflow.task.'+'a'.repeat(64),projectId:'binari',payloadSha256:'b'.repeat(64),claimStamp:1};

test('both typed Planner tasks adapt to one machine-only operation with one target per claim',async()=>{
 const calls=[];
 const pds=localPorts({transport:opts=>{calls.push(opts);return {ok:true};}});
 for(const [task,kind,id,path,field] of [
  [conceptPlan,'concept-category',conceptPlan.input.categoryId,conceptCategoryPath,'categoryId'],
  [npcPlan,'npc-directory',npcPlan.input.npcId,deletePath,'npcId'],
 ]){
  const command=deleteCommandFromPlannedTask(task);
  assert.deepEqual(command,{operation:'file.delete',targets:[{kind,id}]});
  assert.deepEqual(await pds.deleteTargets(command,binding),{ok:true});
  assert.deepEqual(calls.at(-1),{port:8030,path,method:'POST',body:{claim:{...binding,[field]:id}},deadline:120000});
 }
 assert.deepEqual(exactDeleteTarget(deleteCommandFromPlannedTask({...npcPlan,input:{...npcPlan.input,npcId:'npc.'+'a'.repeat(196)}})).id,'npc.'+'a'.repeat(196));
});

test('rejects forged paths, instructions, extra fields and multi-target envelopes before dispatch',async()=>{
 const pds=localPorts({transport:()=>assert.fail('must not dispatch')});
 const command=deleteCommandFromPlannedTask(conceptPlan);
 const target=command.targets[0];
 for(const input of [
  {...command,instructions:'delete all'}, {...command,targets:[]}, {...command,targets:[target,target]},
  {...command,targets:[{...target,id:'/tmp/live'}]}, {...command,targets:[{...target,id:'../other'}]},
  {...command,targets:[{...target,id:'nice title'}]}, {...command,targets:[{...target,id:'foo\\bar'}]},
  {...command,targets:[{...target,id:'category.foo\n'}]}, {...command,targets:[{...target,path:'concept/other'}]},
  {...command,targets:[{...target,kind:'arbitrary-directory'}]}, {...command,operation:'delete'},
 ])await assert.rejects(pds.deleteTargets(input,binding),/WORKFLOW_DELETE_TARGET_INVALID/);
 assert.throws(()=>deleteCommandFromPlannedTask({...conceptPlan,input:{...conceptPlan.input,scope:'all'}}),/WORKFLOW_DELETE_TARGET_INVALID/);
});
