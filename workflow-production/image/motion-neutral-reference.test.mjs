import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {chmodSync,mkdtempSync,realpathSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import test from 'node:test';

import {canonical,sha} from './vendor/planner-f24d23a/envelope.mjs';
import {PlannerStore} from './vendor/planner-f24d23a/planner.mjs';
import {imageTick} from './image-runner.mjs';
import {localPorts,motionImagePath} from './local-ports.mjs';
import {motionImageKey,motionImageOperation,motionImageRequest,motionImageSchema,runMotionImageFromPath} from './motion-neutral-reference.mjs';
import {createNeutralReferenceRenderer,neutralReferenceKind} from './portrait-renderer.mjs';

const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR4nGNgAAIAAAUAAXpeqz8AAAAASUVORK5CYII=','base64');
const hash=sha(png), resultRef='motion.2d.image.'+hash;
function fixture(t){
 const root=realpathSync(mkdtempSync(join(tmpdir(),'neutral-image-test-')));
 const dbPath=join(root,'workflow.sqlite'),store=new PlannerStore(dbPath),db=store.db;
 db.exec('ALTER TABLE workflow_tasks ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0 CHECK(retry_count BETWEEN 0 AND 3)');
 chmodSync(dbPath,0o600);t.after(()=>{store.close();rmSync(root,{recursive:true,force:true});});
 const officialRequestId='request.official.'+'a'.repeat(64);
 const taskId='workflow.task.'+sha(canonical([officialRequestId,motionImageKey]));
 const taskInput={sourcePresetId:'Walk.v1',variantPresetId:'walk-flat'};
 const data={...taskInput,projectId:'binari',schemaVersion:motionImageSchema,label:'걷기'};
 const payload=canonical(data), payloadSha256=sha(payload);
 db.prepare(`INSERT INTO workflow_runs(official_request_id,project_id,request_type,schema_version,payload,payload_sha256,registered_at,state,updated_at)
  VALUES(?,?,?,?,?,?,1,'planned',1)`).run(officialRequestId,'binari',motionImageRequest,motionImageSchema,payload,payloadSha256);
 db.prepare(`INSERT INTO workflow_tasks(task_id,official_request_id,task_key,task_type,worker_type,task_input,depends_on_task_id,updated_at)
  VALUES(?,?,?,?,'image',?,NULL,1)`).run(taskId,officialRequestId,motionImageKey,motionImageOperation,canonical(taskInput));
 const get=()=>db.prepare('SELECT * FROM workflow_tasks WHERE task_id=?').get(taskId);
 return {dbPath,db,taskId,officialRequestId,payloadSha256,get};
}

test('resident Image picks the formal neutral task and publishes exactly one PNG',async t=>{
 const {dbPath,taskId,officialRequestId,payloadSha256,get}=fixture(t), calls=[];
 const pds={capability:async(_s,operation)=>{calls.push(operation);return true;},
  readMotionImage:async claim=>{
   assert.deepEqual(claim,{taskId,officialRequestId,projectId:'binari',payloadSha256,claimStamp:get().updated_at});
   assert.equal(get().status,'running');return {sourcePresetHash:'f'.repeat(64)};
  },
  publishMotionImage:async(_claim,artifact)=>{calls.push('publish');assert.deepEqual(artifact,{sha256:hash,base64:png.toString('base64')});return {ok:true,resultRef};},
 };
 const out=await imageTick({mode:'run',dbPath,pds,providerReady:async()=>true,renderNeutral:async command=>{
  calls.push('render');assert.deepEqual(command,{referenceKind:neutralReferenceKind});return png;
 }});
 assert.deepEqual(out,{state:'completed',taskId,resultRef});assert.equal(get().result_ref,resultRef);
 assert.deepEqual(calls,['npc.portrait-representative',motionImageOperation,'render','publish']);
});

test('tampered task never starts, and missing PDS receipt leaves running for reconciliation',async t=>{
 const {db,dbPath,taskId,officialRequestId,get}=fixture(t);
 const opts={dbPath,taskId,officialRequestId,pds:{readMotionImage:async()=>({sourcePresetHash:'f'.repeat(64)}),
  publishMotionImage:async()=>{throw Error('connection lost');}},renderNeutral:async()=>png};
 const original=get().task_input;
 db.prepare('UPDATE workflow_tasks SET task_input=?').run('{}');
 await assert.rejects(runMotionImageFromPath(opts),/BINDING_INVALID/);
 assert.equal(get().status,'pending');
 db.prepare('UPDATE workflow_tasks SET task_input=?').run(original);
 assert.equal((await runMotionImageFromPath(opts)).state,'requires_reconciliation');
 assert.equal(get().status,'running');
});

test('PDS paths and proof shapes contain no NPC identity',async()=>{
 const routes=[];
 const pds=localPorts({transport:async options=>{routes.push(options);
  return {enabled:true,schemaVersion:'workflow.motion-preset-convert-2d.v1'};}});
 assert.equal(await pds.capability(undefined,motionImageOperation),true);
 const claim={officialRequestId:'fixture',taskId:'image'};
 const artifact={base64:'a',sha256:'b'};
 await pds.readMotionImage(claim);await pds.publishMotionImage(claim,artifact);
 assert.deepEqual(routes.map(({path,method,body})=>[path,method,body]),[
  [motionImagePath,'GET',undefined],[motionImagePath+'/read','POST',{claim}],[motionImagePath,'POST',{claim,artifact}]]);
});

test('native neutral input is fixed and prompt is faceless; no generation invoked',async()=>{
 const renderer=createNeutralReferenceRenderer({hostConfig:'/missing-fixture',hostConfigSha256:'f'.repeat(64)});
 await assert.rejects(renderer({referenceKind:neutralReferenceKind,character:{npcId:'not-allowed'}}),/NEUTRAL_REFERENCE_INVALID/);
 await assert.rejects(renderer({referenceKind:neutralReferenceKind},{signal:AbortSignal.abort()}),/CANCELLED/);
 const native=fileURLToPath(new URL('./portrait-native.py',import.meta.url));
 const proc=spawnSync('python3',['-B','-c',`
import runpy,sys
module=runpy.run_path(sys.argv[1])
prompt=module['neutral_reference_prompt']
print(prompt({'referenceKind':'motion-neutral-2d.v1'}))
try:
  prompt({'referenceKind':'motion-neutral-2d.v1','character':'prohibited'})
except ValueError: pass
else: raise AssertionError()
`,native],{encoding:'utf8'});
 assert.equal(proc.status,0,proc.stderr);
 assert.match(proc.stdout,/exactly one full-body 2D humanoid mannequin/);
 assert.match(proc.stdout,/flat monochrome/);
 assert.match(proc.stdout,/plain faceless/);
 assert.match(proc.stdout,/No costume[^.]*props[^.]*identifying traits/);
});

test('native helper routes neutral reference through one pinned-provider call without an NPC reference',()=>{
 const native=fileURLToPath(new URL('./portrait-native.py',import.meta.url));
 const proc=spawnSync('python3',['-B','-c',`
import base64,hashlib,json,runpy,sys
m=runpy.run_path(sys.argv[1]); main=m['main']
raw=base64.b64decode(sys.argv[2]); calls=[]
def digest(x): return hashlib.sha256(json.dumps(x,sort_keys=True).encode()).hexdigest()
class Pairs:
 @staticmethod
 def descriptor(instruction,manifest):
  assert manifest is None and 'faceless' in instruction
  return {'prompt':instruction,'outputContract':{'alpha':True}}
 @staticmethod
 def reference(*args): raise AssertionError('neutral reference cannot read NPC')
class Runner:
 def generate(self,claim):
  assert not calls and 'referenceManifest' not in claim
  calls.append(claim)
  assert claim['index']==0 and claim['providerRequestHash']==digest(claim['providerRequest'])
  return raw, {'actualToolOutput':True,'sha256':hashlib.sha256(raw).hexdigest(),**claim}
 def execution_evidence(self): return {'outcome':'joined'}
def check_alpha(output,descriptor,use):
 assert output==raw and descriptor=={'alpha':True} and use=='portrait'
main.__globals__['bind']=lambda *_: (lambda *_:Runner(),Pairs(),{},digest,check_alpha)
sys.argv=['portrait-native.py','render-neutral','placeholder','fixture']
main()
assert len(calls)==1
`,native,png.toString('base64')],{input:JSON.stringify({referenceKind:neutralReferenceKind})});
 assert.equal(proc.status,0,String(proc.stderr));
 assert.deepEqual(proc.stdout,png);
});
