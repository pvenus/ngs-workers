import test from 'node:test';
import assert from 'node:assert/strict';
import {createWarmAnimation,warmIdleMs} from './animation-warm.mjs';
const ok={kind:'rendered',result:{frames:['00000.png']}};
const stop={kind:'closed',joined:true};
function fixture({
 reply=()=>ok,close=()=>stop,exited=0,
}={}){
 let time=0,launches=0,dirs=0,removed=0,ended=0,alive=true;
 const commands=[];
 const model=createWarmAnimation({
  now:()=>time,closeWithin:20,makeRoot:async()=>'/private-fixture/root'+(launches+1),
  makeJob:async root=>root+'/job-00000'+(++dirs),
  removeRoot:async()=>{removed++;},
  launch:()=>{launches++;return {
   alive:()=>alive,exited:Promise.resolve(exited),
   end:()=>{ended++;},
   async command(request){commands.push(request);return request.op==='close'?close():reply();},
  };},
 });
 const signal=new AbortController().signal;
 return {model,commands,at:value=>{time=value;},lost:()=>{alive=false;},
  counts:()=>({launches,removed,ended}),run:async(finish=true)=>{
   const result=await model.render(await model.jobDir(),signal);if(finish)model.workFinished();return result;
  }};
}
test('continuous eligible jobs reuse one helper and refresh the timeout from last render',async()=>{
 const f=fixture();assert.equal(f.model.cold(),true);assert.deepEqual(await f.run(),ok.result);
 f.at(warmIdleMs-1);assert.equal(f.model.cold(),false);assert.deepEqual(await f.run(),ok.result);
 f.at(warmIdleMs);assert.equal(await f.model.idle(),true);
 assert.equal(f.counts().launches,1);assert.deepEqual(f.commands.map(x=>x.op),['render','render']);
 assert.notEqual(f.commands[0].job,f.commands[1].job);
 f.at(2*warmIdleMs-2);assert.equal(await f.model.idle(),true);assert.equal(f.model.idleDelay(5000),1);
 f.at(2*warmIdleMs-1);assert.equal(await f.model.idle(),true);assert.equal(f.model.cold(),true);
 assert.deepEqual(f.counts(),{launches:1,removed:1,ended:0});
});
test('runnable work at exact TTL uses existing helper before considering idle',async()=>{
 const f=fixture();await f.run();f.at(warmIdleMs);
 assert.equal(f.model.cold(),false);await f.run();
 assert.equal(f.counts().launches,1);assert.equal(await f.model.idle(),true);
 assert.equal(f.commands.some(x=>x.op==='close'),false);
});
test('verified idle unload leaves resident scheduler reusable for a later cold job',async()=>{
 const f=fixture();await f.run();f.at(warmIdleMs);assert.equal(await f.model.idle(),true);
 assert.equal(f.model.cold(),true);await f.run();assert.equal(f.counts().launches,2);
 assert.equal(await f.model.shutdown(),true);assert.equal(f.counts().removed,2);
});
test('render failure + joined helper exit allows cold next claim; preserves error code',async()=>{
 let calls=0;const f=fixture({reply:()=>++calls===1?{kind:'error',joined:true,code:'ANIMATION_PNG_FORMAT'}:ok});
 await assert.rejects(f.run(),error=>error.message==='ANIMATION_PNG_FORMAT'&&error.rendererJoined===true);
 assert.equal(f.model.cold(),true);await f.run();assert.equal(f.counts().launches,2);
});
test('unknown render stop latches, no new render/no repeat shutdown signal/no scratch erasure',async()=>{
 const f=fixture({reply:()=>({kind:'error',joined:false,code:'ANIMATION_PROCESS_STOP_UNCONFIRMED'})});
 await assert.rejects(f.run(),/STOP_UNCONFIRMED/);assert.throws(f.model.cold,/STOP_UNCONFIRMED/);
 await assert.rejects(f.run(),/STOP_UNCONFIRMED/);assert.equal(await f.model.shutdown(),false);
 assert.deepEqual(f.commands.map(x=>x.op),['render']);assert.deepEqual(f.counts(),{launches:1,removed:0,ended:1});
});
test('idle unconfirmed stop latches and is not reported as unload',async()=>{
 const f=fixture({close:()=>({kind:'closed',joined:false})});await f.run();f.at(warmIdleMs);
 assert.equal(await f.model.idle(),false);assert.equal(await f.model.idle(),false);
 assert.throws(f.model.cold,/STOP_UNCONFIRMED/);assert.equal(f.counts().removed,0);
 assert.deepEqual(f.commands.map(x=>x.op),['render','close']);
});
test('joined claim requires zero-exit helper as well',async()=>{
 const f=fixture({exited:1});await f.run();assert.equal(await f.model.shutdown(),false);
 assert.throws(f.model.cold,/STOP_UNCONFIRMED/);
});
test('unexpected exit while warm blocks new attempts instead of attaching or starting unknown engine',async()=>{
 const f=fixture();await f.run();f.lost();assert.throws(f.model.cold,/STOP_UNCONFIRMED/);
 await assert.rejects(f.run(),/STOP_UNCONFIRMED/);assert.equal(f.counts().launches,1);
});
test('enforces private job basename and pre-start abort',async()=>{
 const f=fixture();const job=await f.model.jobDir();const stop=new AbortController();stop.abort();
 await assert.rejects(f.model.render(job,stop.signal),/CANCELLED/);
 await assert.rejects(f.model.render('/other/job-000001',new AbortController().signal),/JOB_SCOPE/);
 assert.equal(f.counts().launches,0);
});

test('last work includes publication and cleanup, not only rendered reply',async()=>{
 const f=fixture();await f.run(false);f.at(120000);
 f.model.workFinished();
 f.at(warmIdleMs);assert.equal(await f.model.idle(),true);assert.equal(f.counts().removed,0);
 f.at(120000+warmIdleMs);assert.equal(await f.model.idle(),true);assert.equal(f.counts().removed,1);
});
