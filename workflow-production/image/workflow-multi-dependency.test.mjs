import assert from 'node:assert/strict';
import {chmodSync,mkdtempSync,realpathSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {DatabaseSync} from 'node:sqlite';
import test from 'node:test';

for(const family of ['image','document','animation','postprocess','file','audio','candidate-audio-resident']){
 const {nextWorkflowTaskFromPath,workflowTaskEligibleSql}=await import(`../${family}/workflow-ready.mjs`);
 for(const extra of [false,true]){
  test(`${family}: candidate and atomic claim enforce same-request prerequisites (${extra?'five slots':'legacy ledger'})`,t=>{
   const dir=realpathSync(mkdtempSync(join(tmpdir(),'workflow-dependencies-')));
   const dbPath=join(dir,'workflow.sqlite'),db=new DatabaseSync(dbPath);
   chmodSync(dbPath,0o600);
   t.after(()=>{db.close();rmSync(dir,{force:true,recursive:true});});
   db.exec(`CREATE TABLE workflow_runs(official_request_id TEXT PRIMARY KEY,state TEXT,registered_at INTEGER,request_type TEXT,payload TEXT);
    CREATE TABLE workflow_tasks(task_id TEXT PRIMARY KEY,official_request_id TEXT,task_key TEXT,task_type TEXT,
     worker_type TEXT,status TEXT,retry_count INTEGER DEFAULT 0,updated_at INTEGER DEFAULT 1,result_ref TEXT,
     task_input TEXT,depends_on_task_id TEXT);
    INSERT INTO workflow_runs VALUES('req','planned',1,'fixture','{}');
    INSERT INTO workflow_tasks(task_id,official_request_id,task_key,task_type,worker_type,status,depends_on_task_id) VALUES
     ('child','req','child','work','worker','pending','a'),
     ('a','req','parent','work','worker','pending',NULL),
     ('b','req','p2','work','worker','completed',NULL),
     ('c','req','p3','work','worker','completed',NULL),
     ('d','req','p4','work','worker','completed',NULL),
     ('e','req','p5','work','worker','completed',NULL),
     ('wrong','other-req','p5','work','worker','completed',NULL);`);
   if(extra){
    for(let i=2;i<=5;i++)db.exec('ALTER TABLE workflow_tasks ADD COLUMN depends_on_task_id_'+i+' TEXT');
    db.exec("UPDATE workflow_tasks SET depends_on_task_id_2='b',depends_on_task_id_3='c',depends_on_task_id_4='d',depends_on_task_id_5='wrong' WHERE task_id='child'");
   }
   const find=()=>nextWorkflowTaskFromPath(dbPath,{workerType:'worker',taskType:'work',taskKey:'child',predecessorKey:'parent'});
   const claim=db.prepare(`UPDATE workflow_tasks SET status='running' WHERE task_id='child' AND ${workflowTaskEligibleSql(db)}`);
   assert.equal(find(),null);
   assert.equal(claim.run().changes,0);
   const status=db.prepare('UPDATE workflow_tasks SET status=? WHERE task_id=?');
   status.run('completed','a');
   if(extra){
    assert.equal(find(),null);
    assert.equal(claim.run().changes,0);
    db.exec("UPDATE workflow_tasks SET depends_on_task_id_5='e' WHERE task_id='child'");
   }
   assert.equal(find()?.taskId,'child');
   // The candidate is only advisory: a predecessor can change before the exact UPDATE.
   status.run('failed',extra?'e':'a');
   assert.equal(claim.run().changes,0);
   status.run('completed',extra?'e':'a');
   assert.equal(claim.run().changes,1);
   assert.equal(claim.run().changes,0);
  });
 }
}
