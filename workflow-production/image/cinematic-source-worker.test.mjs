import assert from 'node:assert/strict';
import test from 'node:test';
import {DatabaseSync} from 'node:sqlite';
import {mkdtempSync,realpathSync,rmSync,writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {crc32,deflateSync} from 'node:zlib';
import {canonical,sha} from './vendor/planner-f24d23a/envelope.mjs';
import {imageTick} from './image-runner.mjs';
import {sourceType,sourceSchema,sourceKey} from './cinematic-source-worker.mjs';

const input={title:'빈 마루',prompt:'저녁의 마당, 바람에 흔들리는 한 점의 불빛.'};
const officialRequestId='request.official.'+'5'.repeat(64);
const taskId='workflow.task.'+sha(canonical([officialRequestId,sourceKey]));
const u32=n=>{const b=Buffer.alloc(4);b.writeUInt32BE(n);return b;};
const chunk=(tag,raw)=>{
 const b=Buffer.concat([Buffer.from(tag),raw]);return Buffer.concat([u32(raw.length),b,u32(crc32(b))]);
};
const png=Buffer.concat([Buffer.from('89504e470d0a1a0a','hex'),
 chunk('IHDR',Buffer.concat([u32(1536),u32(1024),Buffer.from([8,2,0,0,0])])),
 chunk('IDAT',deflateSync(Buffer.alloc(1024*(1536*3+1)))),
 chunk('IEND',Buffer.alloc(0))]);
const resultRef='binari/cinematics/sources/'+sha(png)+'.json';
function fixture(t,request=input){
 const dir=realpathSync(mkdtempSync(join(tmpdir(),'cinematic-source-stage-')));
 const dbPath=join(dir,'db.sqlite');writeFileSync(dbPath,'',{mode:0o600});
 const db=new DatabaseSync(dbPath);
 t.after(()=>{db.close();rmSync(dir,{recursive:true,force:true});});
 db.exec(`CREATE TABLE workflow_runs(official_request_id,project_id,request_type,schema_version,payload,payload_sha256,registered_at,state,updated_at,reason);
  CREATE TABLE workflow_tasks(task_id,official_request_id,task_key,task_type,worker_type,task_input,result_ref,updated_at,depends_on_task_id,status,retry_count);`);
 const payload=canonical({schemaVersion:sourceSchema,projectId:'binari',...request});
 db.prepare("INSERT INTO workflow_runs VALUES(?,?,?,?,?,?,1,'planned',1,NULL)").run(officialRequestId,'binari',sourceType,sourceSchema,payload,sha(payload));
 db.prepare("INSERT INTO workflow_tasks VALUES(?,?,?,?,?,?,NULL,2,NULL,'pending',0)").run(taskId,officialRequestId,sourceKey,sourceType,'image',canonical(request));
 const state=()=>db.prepare('SELECT status,result_ref,updated_at FROM workflow_tasks').get();
 return {db,dbPath,state,payload};
}
const safeCheck=async()=>({ready:true});
test('resident claims an image-only job and retries exact PNG bytes only after lost ACK, then completes',async t=>{
 const f=fixture(t),order=[];
 const pds={capability:async(_signal,operation)=>{assert.equal(operation,sourceType);return true;},
 readCinematicSource:async claim=>{order.push('read');assert.deepEqual(claim,{officialRequestId,taskId,projectId:'binari',
  payloadSha256:sha(f.payload),claimStamp:f.state().updated_at});return input;},
 publishCinematicSource:async(claim,artifact)=>{
  order.push('publish');assert.equal(claim.claimStamp,f.state().updated_at);
  assert.deepEqual(artifact,{sha256:sha(png),bytes:png.length,width:1536,height:1024,base64:png.toString('base64')});
  if(order.length===3)throw Error('lost');return {ok:true,resultRef};
 }};
 const renderCinematicSource=async direction=>{order.push('render');assert.deepEqual(direction,input);return png;};
 assert.equal((await imageTick({...f,mode:'run',pds,renderCinematicSource},{check:safeCheck})).state,'completed');
 assert.deepEqual(order,['read','render','publish','publish']);
 assert.equal(f.state().result_ref,resultRef);
 assert.equal((await imageTick({...f,mode:'run',pds,renderCinematicSource},{check:safeCheck})).state,'idle');
});
test('mismatched PDS input has no side effects; ambiguous native invocation is not rerun',async t=>{
 for(const mismatched of [true,false]){
  const f=fixture(t);let gen=0;
  const pds={capability:async()=>true,readCinematicSource:async()=>mismatched?{...input,title:'다른 제목'}:input,
   publishCinematicSource:async()=>assert.fail('must not publish')};
  const renderCinematicSource=async()=>{gen++;throw Error('unknown model outcome');};
  const options={...f,mode:'run',pds,renderCinematicSource};
  assert.equal((await imageTick(options,{check:safeCheck})).state,mismatched?'failed':'requires_reconciliation');
  if(!mismatched)assert.equal((await imageTick(options,{check:safeCheck})).state,'idle');
  assert.equal(gen,mismatched?0:1);
 }
});
test('Python trusted bridge sends one opaque native descriptor and skips alpha validation',()=>{
 const helper=fileURLToPath(new URL('./portrait-native.py',import.meta.url));
 const script=String.raw`
import runpy,sys,json,hashlib
main=runpy.run_path(sys.argv[1])["main"]
def digest(x): return hashlib.sha256(json.dumps(x,sort_keys=True).encode()).hexdigest()
class Runner:
 def generate(self,claim):
  d=claim["providerRequest"]; assert d["arguments"]["transparent_background"] is False
  assert "1536×1024" in d["arguments"]["prompt"] and "빈 마루" in d["arguments"]["prompt"]
  raw=b'not-a-generated-image-in-this-bridge-test'
  return raw,{**claim,"sha256":hashlib.sha256(raw).hexdigest(),"actualToolOutput":True}
 def execution_evidence(self): return {"outcome":"joined"}
def prohibited(*a): raise AssertionError("must not invoke portrait/alpha")
main.__globals__["bind"]=lambda *_:(prohibited,lambda *_:Runner(),None,None,digest,prohibited)
sys.argv=["helper","render-cinematic-source","config","pin"]
main()
`;
 const p=spawnSync('/usr/bin/python3',['-B','-c',script,helper],{input:JSON.stringify(input),encoding:'utf8'});
 assert.equal(p.status,0,p.stderr);
 assert.equal(p.stdout,'not-a-generated-image-in-this-bridge-test');
});
