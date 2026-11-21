import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {createHash} from 'node:crypto';
import {markWorkflowTaskFailed} from './workflow-ready.mjs';
import {markFailed,readStopDiagnostic} from './animation-cli.mjs';
const source=await readFile(new URL('./animation-cli.mjs',import.meta.url),'utf8');
const executeSrc=source.slice(source.indexOf('async function execute('),source.indexOf('if(process.argv[1]==='));
const t={taskId:'workflow.task.'+'a'.repeat(64),officialRequestId:'request.official.fixture',motion:'walk',direction:'s'};
const dto={taskId:t.taskId,officialRequestId:t.officialRequestId,claimStamp:64,projectId:'fixture',npcId:'npc.fixture'};
const portrait=Buffer.from('portrait'),motion=Buffer.from('motion'),frame=Buffer.from('png-fixture');
const sha=b=>createHash('sha256').update(b).digest('hex');
const failureArgs=results=>results.find(x=>x[0]==='failure-update')?.slice(2);

async function fixture(opts={}){
 const ops=[],logs=[];const abort=new AbortController();
 const db={prepare(sql){return{run(...args){
  const failure=sql.includes("status='failed'");
  ops.push([failure?'failure-update':'commit-update',sql,...args]);
  if(failure&&opts.updateThrow)throw Error('/private/path');
  if(!failure&&opts.commitThrow)throw Error('/private/path');
  return {changes:failure?(opts.updateChanges??1):(opts.commitChanges??1)};
 }}}};
 const f=Function('claim','tmpdir','mkdtemp','join','local','path','insist','sha','writeFile','readFile','runNative','markFailed','readStopDiagnostic','rm','console',executeSrc+';return execute;')(
 ()=>opts.noclaim?null:{dto,intent:{}},()=>'/tmp',async()=>'/fixture',join,async(path)=>{
  ops.push([path]);
  if(path==='/animation/read'){
   if(opts.readCancel){abort.abort();abort.signal.throwIfAborted();}
   if(opts.readError)throw Error('ANIMATION_FIXTURE_READ');
   return {portraitAssetId:sha(portrait)+'.png',motion:'walk',direction:'s'};
  }
  if(path==='/animation/asset')return portrait;
  if(path==='/v1/motion-presets/walk/s')return{policy:{},base64:motion.toString('base64'),bytes:motion.length,sha256:sha(motion),assetId:sha(motion)+'.mp4',direction:'s',presetId:'walk',frameCount:1,fps:24};
  if(path==='/animation'){
   if(opts.publishError)throw Error('ANIMATION_PDS_HTTP_504');
   return opts.badAck?{}:{ok:true};
  }
  throw Error('unexpected endpoint');
 },'/animation',(val,code)=>{if(!val)throw Error(code);},sha,async()=>{},async()=>{
  if(opts.cancelAtValidation)abort.abort();
  return frame;
 },async()=>{
  ops.push(['render']);
  if(opts.renderError){
   const e=Error(opts.renderError);
   if(opts.renderJoined!==undefined)e.rendererJoined=opts.renderJoined;
   if(opts.cancelAtRender)abort.abort();
   throw e;
  }
  return {frames:['00000.png'],frameHashes:[sha(frame)],fps:24,width:opts.badFrame?1:768,height:432};
 },markFailed,async()=>{ops.push(['sidecar']);return opts.diag??null;},
 async()=>{ops.push(['rm']);if(opts.rmError)throw Error('/private/path');},
 {log:line=>logs.push(JSON.parse(line)),error:line=>logs.push(JSON.parse(line))});
 await f(db,t,abort.signal);
 return {ops,logs,dec:logs.find(x=>x.state==='failed'||x.state==='requires_reconciliation')};
}
function counts(ops,name){return ops.filter(x=>x[0]===name).length;}
function assertFailed({ops,dec},stage){
 assert.equal(dec.state,'failed');assert.equal(dec.stage,stage);
 assert.equal(counts(ops,'failure-update'),1);assert.equal(counts(ops,'/animation'),0);
 const update=ops.find(x=>x[0]==='failure-update');
 assert.match(update[1],/worker_type=\?.+status='running'/s);assert.match(update[1],/result_ref IS NULL/);
 assert.equal(update[2]>=65,true);
 assert.deepEqual(failureArgs(ops).slice(1),[t.taskId,t.officialRequestId,'animation',64]);
}
test('exact imported shared API; old unjoined renderer still terminal, stop unknown separate',async()=>{
 assert.equal(typeof markWorkflowTaskFailed,'function');assert.equal(typeof readStopDiagnostic,'function');
 const f=await fixture({renderError:'ANIMATION_PROCESS_STOP_UNCONFIRMED'});
 assertFailed(f,'render');assert.equal(f.dec.rendererStop,'unknown');
});
test('explicit JOINED still terminal and stop confirmed',async()=>{
 const f=await fixture({renderError:'ANIMATION_PNG_FORMAT',renderJoined:true});
 assertFailed(f,'render');assert.equal(f.dec.rendererStop,'confirmed');
});
test('post-claim failure and cancellation before publication terminal once',async()=>{
 for(const [options,stage] of [[{readError:true},'read'],[{readCancel:true},'read'],
   [{renderError:'ANIMATION_CANCELLED',cancelAtRender:true},'render'],[{badFrame:true},'validate'],
   [{cancelAtValidation:true},'validate']])assertFailed(await fixture(options),stage);
});
test('publication send/ack uncertainty and DB commit uncertainty are not failed',async()=>{
 for(const [options,stage] of [[{publishError:true},'publish'],[{badAck:true},'publish'],
  [{commitThrow:true},'commit'],[{commitChanges:0},'commit']]){
  const f=await fixture(options);
  assert.equal(f.dec.state,'requires_reconciliation');assert.equal(f.dec.stage,stage);
  assert.equal(counts(f.ops,'/animation'),1);assert.equal(counts(f.ops,'failure-update'),0);
 }
});
test('stale claim or errors in failure update never assert success or retry',async()=>{
 for(const [options,code] of [[{updateChanges:0},'claim_mismatch'],[{updateThrow:true},'update_unconfirmed']]){
  const f=await fixture({readError:true,...options});
  assert.equal(f.dec.state,'requires_reconciliation');assert.equal(f.dec.failureUpdate,code);
  assert.equal(counts(f.ops,'failure-update'),1);assert.equal(counts(f.ops,'/animation'),0);
 }
});
test('stop metadata read before cleanup and scratch error cannot hide terminal status',async()=>{
 const diag={renderOutcome:'returned'};
 const f=await fixture({renderError:'ANIMATION_PROCESS_STOP_UNCONFIRMED',diag,rmError:true});
 assertFailed(f,'render');assert.deepEqual(f.dec.stopDiagnostic,diag);
 assert(f.ops.findIndex(x=>x[0]==='sidecar')<f.ops.findIndex(x=>x[0]==='rm'));
 assert.equal(f.logs.at(-1).code,'ANIMATION_SCRATCH_CLEANUP_FAILED');
 assert(!JSON.stringify(f.logs).includes('/private/path'));
});
test('no claim does not alter state; success commits once',async()=>{
 const none=await fixture({noclaim:true});assert.deepEqual(none.ops,[]);
 const f=await fixture({rmError:true});
 assert.equal(counts(f.ops,'/animation'),1);assert.equal(counts(f.ops,'commit-update'),1);
 assert.equal(counts(f.ops,'failure-update'),0);
 assert.deepEqual(f.logs.filter(x=>x.state).map(x=>x.state),['completed','cleanup_failed']);
});
