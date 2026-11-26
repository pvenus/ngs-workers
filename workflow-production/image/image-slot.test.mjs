import assert from 'node:assert/strict';
import test from 'node:test';
import {imageTick} from './image-runner.mjs';
test('lost claim refreshes immediately and can take the next job within the same tick',async()=>{
 let polls=0;const seen=[];
 const a={taskId:'a'},b={taskId:'b'};
 const result=await imageTick({mode:'run'},{check:async()=>({ready:true}),select:()=>++polls===1?[a]:[a,b],
  execute:async x=>{seen.push(x.taskId);return {state:x.taskId==='a'?'not_claimable':'completed'};}});
 assert.equal(result.state,'completed');assert.deepEqual(seen,['a','b']);assert.equal(polls,2);
});
test('a sole contended head does not become failed or spin',async()=>{
 let polls=0, executions=0;
 const result=await imageTick({mode:'run'},{check:async()=>({ready:true}),select:()=>{polls++;return [{taskId:'same'}];},
  execute:async()=>{executions++;return {state:'not_claimable'};}});
 assert.equal(result.state,'idle');assert.equal(executions,1);assert.equal(polls,2);
});
import {mkdtempSync,writeFileSync,rmSync,realpathSync,lstatSync} from 'node:fs';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {createImageSlot} from './image-slot.mjs';
test('slots have separate scratch, heartbeat identities and pause leases',t=>{
 const base=realpathSync(mkdtempSync(join(tmpdir(),'ngs-image-slot-')));
 t.after(()=>rmSync(base,{force:true,recursive:true}));
 const one=createImageSlot('1',{base}),two=createImageSlot('2',{base}),same=createImageSlot('1',{base});
 assert.notEqual(one.tmp,two.tmp);assert.notEqual(one.tmp,same.tmp);
 assert.equal(one.serverName,'image-worker');assert.equal(two.serverName,'image-worker-2');
 assert.equal(lstatSync(two.tmp).mode&0o077,0);
 const release=one.tryEnter();assert.ok(release);assert.equal(same.tryEnter(),null);
 const other=two.tryEnter();assert.ok(other);
 writeFileSync(join(base,'1','pause'),'test-deploy',{mode:0o600});
 release();assert.equal(one.tryEnter(),null);
 other();assert.ok(two.tryEnter());
 one.cleanup();assert.equal(lstatSync(two.tmp).isDirectory(),true);
});
