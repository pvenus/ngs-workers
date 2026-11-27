import test from 'node:test';
import assert from 'node:assert/strict';
import {existsSync,mkdtempSync,readFileSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {DatabaseSync} from 'node:sqlite';
import {isSqliteBusyExhausted,retrySqliteBusy} from './sqlite-busy-retry.mjs';

const task='workflow.task.'+'a'.repeat(64);
test('worker releases carry the identical shared retry policy',()=>{
 const source=readFileSync(new URL('./sqlite-busy-retry.mjs',import.meta.url));
 for(const role of ['document','image','file','animation','postprocess','audio','candidate-audio-resident'])
  assert.deepEqual(readFileSync(new URL('../'+role+'/sqlite-busy-retry.mjs',import.meta.url)),source,role);
});

test('only busy retries; attempts stop after ten five-second waits and one safe log',async()=>{
 const busy=Object.assign(Error('internal error text'),{errcode:5|(2<<8)});
 const sleep=[],logs=[];
 let attempts=0;
 await assert.rejects(retrySqliteBusy(()=>{attempts++;throw busy;},task,{
  sleep:ms=>sleep.push(ms),log:text=>logs.push(JSON.parse(text))}),error=>error===busy);
 assert.equal(attempts,11);
 assert.deepEqual(sleep,Array(10).fill(5000));
 assert.deepEqual(logs,[{event:'workflow_sqlite_busy_exhausted',taskId:task,phase:'record-completion',retryCount:10,waitMs:5000}]);
 assert.equal(isSqliteBusyExhausted(busy),true);
 for(const error of [Error('SQLITE_BUSY'),Object.assign(Error('lock'),{errcode:6}),Object.assign(Error('constraint'),{errcode:19})]){
  await assert.rejects(retrySqliteBusy(()=>{throw error;},task,{sleep:()=>assert.fail('must not wait'),log:()=>assert.fail('must not log')}),e=>e===error);
  assert.equal(isSqliteBusyExhausted(error),false);
 }
 assert.equal(await retrySqliteBusy(()=>false,task,{sleep:()=>assert.fail('CAS mismatch must not wait')}),false);
});

test('a real SQLite reader blocking a different task commit is released during the wait; only the completion transaction repeats',async t=>{
 const dir=mkdtempSync(join(tmpdir(),'worker-sqlite-busy-')),path=join(dir,'workflow.sqlite');
 const writer=new DatabaseSync(path),reader=new DatabaseSync(path,{readOnly:true});
 t.after(()=>{reader.close();writer.close();if(existsSync(dir))rmSync(dir,{recursive:true,force:true});});
 writer.exec("PRAGMA busy_timeout=0; CREATE TABLE jobs(id TEXT PRIMARY KEY,status TEXT); INSERT INTO jobs VALUES('A','running'),('B','running')");
 reader.exec('BEGIN');
 assert.equal(reader.prepare("SELECT status FROM jobs WHERE id='A'").get().status,'running');
 let attempts=0,waits=0;
 await retrySqliteBusy(()=>{
  attempts++;
  writer.exec('BEGIN IMMEDIATE');
  try{
   assert.equal(writer.prepare("UPDATE jobs SET status='completed' WHERE id='B' AND status='running'").run().changes,1);
   writer.exec('COMMIT');
  }catch(error){writer.exec('ROLLBACK');throw error;}
 },task,{sleep(ms){
  waits++;assert.equal(ms,5000);
  assert.equal(reader.prepare("SELECT status FROM jobs WHERE id='B'").get().status,'running');
  reader.exec('ROLLBACK');
 },log:()=>assert.fail('contention was resolved')});
 assert.equal(attempts,2);assert.equal(waits,1);
 assert.equal(writer.prepare("SELECT status FROM jobs WHERE id='B'").get().status,'completed');
});

test('worker terminal transition clears an existing PDS save marker atomically', async()=>{
 const {DatabaseSync}=await import('node:sqlite');
 const {saveReservationClearSql}=await import('./sqlite-busy-retry.mjs');
 const db=new DatabaseSync(':memory:');
 try {
  db.exec("CREATE TABLE workflow_tasks (status TEXT, save_started_at INTEGER CHECK(save_started_at IS NULL OR status='running')); INSERT INTO workflow_tasks VALUES('running',10)");
  assert.throws(()=>db.exec("UPDATE workflow_tasks SET status='completed'"));
  assert.equal(db.prepare(`UPDATE workflow_tasks SET status='completed'${saveReservationClearSql(db)}`).run().changes,1);
  assert.equal(db.prepare('SELECT save_started_at FROM workflow_tasks').get().save_started_at,null);
 } finally {db.close();}
});
