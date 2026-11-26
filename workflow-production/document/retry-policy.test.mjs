import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import test from 'node:test';
import {workflowTaskEligibleSql as document,documentTaskEligibleSql} from './workflow-ready.mjs';
test('one minute and 30 attempts apply only to fast tasks; exact old limits stay intact',()=>{
 const db=new DatabaseSync(':memory:');
 try{
  db.exec('CREATE TABLE workflow_tasks (status TEXT, retry_count INT, retry_limit INT, updated_at INT)');
  const put=db.prepare('INSERT INTO workflow_tasks VALUES (?,?,?,?)');
  const now=Date.now();
  const rows=[['failed',3,30,now-59_000],['failed',3,30,now-61_000],['failed',29,30,now-61_000],
    ['failed',30,30,now-61_000],['failed',3,3,now-700_000],['failed',2,3,now-61_000],['failed',2,3,now-601_000]];
  for(const row of rows)put.run(...row);
  for(const eligible of [document])
   assert.deepEqual(db.prepare(`SELECT rowid FROM workflow_tasks WHERE ${eligible(db)}`).all().map(r=>r.rowid),[2,3,7]);
 }finally{db.close();}
});
test('new document category inputs may retry known failures before publication',()=>{
 const db=new DatabaseSync(':memory:');
 try{
  db.exec(`CREATE TABLE workflow_tasks(status TEXT,retry_count INT,retry_limit INT,updated_at INT,task_type TEXT);
  CREATE TABLE workflow_runs(request_type TEXT,payload TEXT);
  INSERT INTO workflow_runs VALUES('npc.planning.add','{}');
  INSERT INTO workflow_tasks VALUES('failed',0,30,0,'npc.planning.add'),('failed',0,3,0,'npc.planning.add');`);
  assert.deepEqual(db.prepare(`SELECT t.rowid AS id FROM workflow_tasks t CROSS JOIN workflow_runs r WHERE ${documentTaskEligibleSql(db)}`).all().map(r=>r.id),[1]);
 }finally{db.close();}
});
