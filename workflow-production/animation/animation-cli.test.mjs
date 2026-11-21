import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,realpathSync,rmSync,chmodSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {PlannerStore} from './vendor/planner-f24d23a/planner.mjs';
import {canonical,sha} from './vendor/planner-f24d23a/envelope.mjs';
import {choose,claim,markFailed} from './animation-cli.mjs';
test('only native animation after completed matching directional portrait is selected and claimed once',t=>{
 const dir=realpathSync(mkdtempSync(join(tmpdir(),'workflow-wan-db-')));
 const file=join(dir,'workflow.sqlite'), s=new PlannerStore(file);chmodSync(file,0o600);
 s.db.exec('ALTER TABLE workflow_tasks ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0 CHECK(retry_count BETWEEN 0 AND 3)');
 t.after(()=>{s.close();rmSync(dir,{recursive:true,force:true});});
 const payload={schemaVersion:'ngs.npc-one-stop.replace.v1',projectId:'binari',intent:'official figure portrait',
  character:{npcId:'npc.ch1_dajeong',name:'다정',slug:'dajeong',role:'fixture',actId:'act1',chapterId:'ch1'},
  sourceRefs:['story.fixture'],includeCombat:false};
 const officialRequestId='request.official.'+sha('animation-run');
 s.stage({officialRequestId,projectId:'binari',requestType:'npc.workflow',schemaVersion:payload.schemaVersion,
  payload,payloadSha256:sha(canonical(payload)),status:'registered',registeredAt:1});
 s.acknowledge(officialRequestId);
 assert.equal(choose(file),undefined);
 s.db.prepare("UPDATE workflow_tasks SET status='completed' WHERE task_key='portrait_e'").run();
 const task=choose(file);assert.equal(task.key,'animation_walk_e');
 const selected=claim(s.db,task);assert.equal(selected.dto.npcId,payload.character.npcId);assert.equal(claim(s.db,task),null);
 assert.equal(markFailed(s.db,{...selected.dto,claimStamp:selected.dto.claimStamp+1}),false);
 assert.equal(markFailed(s.db,selected.dto),true);
 assert.equal(markFailed(s.db,selected.dto),false);
 assert.equal(choose(file).key,'animation_run_e');
 assert.equal(s.db.prepare("SELECT status FROM workflow_tasks WHERE task_key='animation_walk_n'").get().status,'pending');
});
