import test from 'node:test';
import assert from 'node:assert/strict';
import {settleFailure} from './failure-state.mjs';

test('only a busy failure transition is retried; no retry after CAS miss or other error',async()=>{
 const waits=[];let writes=0;
 const sleep=ms=>{waits.push(ms);};
 assert.equal(await settleFailure(()=>{
  if(++writes<3)throw Object.assign(Error('busy'),{errcode:517});
  return true;
 },{sleep}),true);
 assert.deepEqual(waits,[100,300]);
 assert.equal(await settleFailure(()=>false,{sleep}),false);
 await assert.rejects(settleFailure(()=>{throw Error('other');},{sleep}),{message:'other'});
 assert.deepEqual(waits,[100,300]);
});
