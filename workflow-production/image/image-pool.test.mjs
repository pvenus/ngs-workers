import assert from 'node:assert/strict';
import test from 'node:test';
import {EventEmitter} from 'node:events';
import {startImagePool} from './image-pool.mjs';
import {imageTick} from './image-runner.mjs';
const sleep=ms=>new Promise(ok=>setTimeout(ok,ms));
const wait=async predicate=>{for(let i=0;i<100&&!predicate();i++)await sleep(5);assert.ok(predicate());};
test('requires both lanes for readiness; replaces only a lost lane and drains both',async t=>{
 const spawned=[],updates=[];
 const pool=startImagePool({entry:'/not-executed',args:['--run','--db','/fixture'],onReady:v=>updates.push(v),restartDelay:0,
  spawn:(_entry,args,opts)=>{
   assert.deepEqual(args,['--run','--db','/fixture']);
   const c=new EventEmitter();
   c.pid=100+spawned.length;c.connected=true;c.lane=opts.env.NGS_IMAGE_POOL_LANE;
   c.send=(m,cb)=>{assert.equal(m.type,'image-pool-drain');c.drained=true;cb?.(null);queueMicrotask(()=>c.emit('exit',0,null));};
   c.kill=()=>c.emit('exit',1,null);
   spawned.push(c);return c;
  }});
 t.after(()=>pool.stop());
 const ready=c=>c.emit('message',{type:'image-lane-health',ready:true});
 assert.deepEqual(spawned.map(x=>x.lane),['1','2']);ready(spawned[0]);assert.deepEqual(updates,[]);
 ready(spawned[1]);assert.deepEqual(updates,[true]);
 spawned[1].emit('exit',1,null);assert.deepEqual(updates,[true,false]);
 await wait(()=>spawned.length===3);
 assert.equal(spawned[2].lane,'2');ready(spawned[2]);assert.deepEqual(updates,[true,false,true]);
 await pool.stop();assert.equal(spawned[0].drained,true);assert.equal(spawned[2].drained,true);assert.deepEqual(updates,[true,false,true,false]);
});
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
