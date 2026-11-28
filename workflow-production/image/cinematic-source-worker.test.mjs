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
import {sourceType,sourceSchema,sourceKey,cinematicDesignId,selectCinematicDesign,backgroundSourceType,backgroundDesignId,validBackgroundRenderInput} from './cinematic-source-worker.mjs';

const input={title:'빈 마루',prompt:'저녁의 마당, 바람에 흔들리는 한 점의 불빛.'};
const title='시네마틱 영상 디자인',markdown='# '+title+'\n\n## 먹선과 형태\n\n마른 붓의 굵은 먹선과 큰 여백.\n';
const design={categoryId:cinematicDesignId,markdown,sha256:sha(markdown)};
const concept=()=>({document:{projectId:'ProjectBS',hierarchy:{groups:[{id:'group.art-design',categoryIds:[cinematicDesignId,'other']}],
 archivedCategoryIds:[]},sections:[{id:cinematicDesignId,title,fields:[{id:'content',value:markdown}]},
  {id:'other',title:'다른 디자인',fields:[{id:'content',value:'exclude-this-other-section'}]}]}});
const officialRequestId='request.official.'+'5'.repeat(64);
const taskId='workflow.task.'+sha(canonical([officialRequestId,sourceKey]));
const u32=n=>{const b=Buffer.alloc(4);b.writeUInt32BE(n);return b;};
const chunk=(tag,raw)=>{
 const b=Buffer.concat([Buffer.from(tag),raw]);return Buffer.concat([u32(raw.length),b,u32(crc32(b))]);
};
const image=(h, rows=Buffer.alloc(h*(1536*3+1)))=>Buffer.concat([Buffer.from('89504e470d0a1a0a','hex'),
 chunk('IHDR',Buffer.concat([u32(1536),u32(h),Buffer.from([8,2,0,0,0])])),
 chunk('IDAT',deflateSync(rows)),chunk('IEND',Buffer.alloc(0))]);
const png=image(768),backgroundPng=image(1024);
const resultRef='binari/cinematics/sources/'+sha(png)+'.json';
function fixture(t,request=input,background=false){
 const dir=realpathSync(mkdtempSync(join(tmpdir(),'cinematic-source-stage-')));
 const dbPath=join(dir,'db.sqlite');writeFileSync(dbPath,'',{mode:0o600});
 const db=new DatabaseSync(dbPath);
 t.after(()=>{db.close();rmSync(dir,{recursive:true,force:true});});
 db.exec(`CREATE TABLE workflow_runs(official_request_id,project_id,request_type,schema_version,payload,payload_sha256,registered_at,state,updated_at,reason);
  CREATE TABLE workflow_tasks(task_id,official_request_id,task_key,task_type,worker_type,task_input,result_ref,updated_at,depends_on_task_id,status,retry_count,portrait_read_owner_pid INTEGER,save_started_at INTEGER);`);
 const mode=typeof background==='string'?background:null;
 const type=mode?'ui.asset.'+mode:background?backgroundSourceType:sourceType,
  schema=mode?'ngs.ui-asset-'+mode+'.v1':background?'ngs.background-source-generate.v1':sourceSchema,
  key=mode?'ui_asset_'+mode:background?'background_source_generate':sourceKey;
 const id='workflow.task.'+sha(canonical([officialRequestId,key]));
 const payload=canonical({schemaVersion:schema,projectId:'binari',...request});
 db.prepare("INSERT INTO workflow_runs VALUES(?,?,?,?,?,?,1,'planned',1,NULL)").run(officialRequestId,'binari',type,schema,payload,sha(payload));
 db.prepare("INSERT INTO workflow_tasks VALUES(?,?,?,?,?,?,NULL,2,NULL,'pending',0,NULL,NULL)").run(id,officialRequestId,key,type,'image',canonical(request));
 const state=()=>db.prepare('SELECT status,result_ref,updated_at,retry_count,portrait_read_owner_pid FROM workflow_tasks').get();
 return {db,dbPath,state,payload};
}
const safeCheck=async()=>({ready:true});
test('resident claims an image-only job and retries exact PNG bytes only after lost ACK, then completes',async t=>{
 const f=fixture(t),order=[];
 const pds={capability:async(_signal,operation)=>{assert.equal(operation,sourceType);return true;},
 readCinematicSource:async claim=>{order.push('read');assert.deepEqual(claim,{officialRequestId,taskId,projectId:'binari',
  payloadSha256:sha(f.payload),claimStamp:f.state().updated_at});return input;},
 readCinematicConcept:async()=>{order.push('design');return concept();},
 publishCinematicSource:async(claim,artifact)=>{
  order.push('publish');assert.equal(f.state().portrait_read_owner_pid,null);assert.equal(claim.claimStamp,f.state().updated_at);
  assert.deepEqual(artifact,{sha256:sha(png),bytes:png.length,width:1536,height:768,base64:png.toString('base64')});
  if(order.length===4)throw Error('lost');return {ok:true,resultRef};
 }};
 const renderCinematicSource=async direction=>{order.push('render');assert.equal(f.state().portrait_read_owner_pid,process.pid);assert.deepEqual(direction,{...input,design});return png;};
 const result=await imageTick({...f,mode:'run',pds,renderCinematicSource},{check:safeCheck});
 assert.equal(result.state,'completed');assert.equal(result.designSha256,design.sha256);
 assert.deepEqual(order,['read','design','render','publish','publish']);
 assert.equal(f.state().result_ref,resultRef);
 assert.equal((await imageTick({...f,mode:'run',pds,renderCinematicSource},{check:safeCheck})).state,'idle');
});
test('local native or input failure records normal failure and does not immediately rerun',async t=>{
 for(const mismatched of [true,false]){
  const f=fixture(t);let gen=0;
  const pds={capability:async()=>true,readCinematicSource:async()=>mismatched?{...input,title:'다른 제목'}:input,
   readCinematicConcept:async()=>concept(),publishCinematicSource:async()=>assert.fail('must not publish')};
  const renderCinematicSource=async()=>{gen++;throw Error('unknown model outcome');};
  const options={...f,mode:'run',pds,renderCinematicSource};
  const r=await imageTick(options,{check:safeCheck});
  assert.equal(r.state,'failed');assert.equal(f.state().retry_count,0);assert.equal(f.state().portrait_read_owner_pid,null);
  assert.equal(r.diagnostic.stage,mismatched?'read-inputs':'render');
  if(!mismatched)assert.equal((await imageTick(options,{check:safeCheck})).state,'idle');
  assert.equal(gen,mismatched?0:1);
 }
});
test('selects the registered active design only and fails closed without worker fallback',async t=>{
 assert.deepEqual(selectCinematicDesign(concept()),design);
 const badCases=[
  doc=>{doc.projectId='other-project';},
  doc=>{doc.sections[0].id='some-other-style';},
  doc=>{doc.hierarchy.archivedCategoryIds.push(cinematicDesignId);},
  doc=>{doc.hierarchy.groups[0].categoryIds=[];},
  doc=>{doc.sections[0].fields[0].value='missing markdown title';},
 ];
 for(const mutate of badCases){
  const source=concept();mutate(source.document);const f=fixture(t);
  const pds={capability:async()=>true,readCinematicSource:async()=>input,readCinematicConcept:async()=>source,
   publishCinematicSource:async()=>assert.fail('must not publish')};
  const result=await imageTick({...f,mode:'run',pds,renderCinematicSource:()=>assert.fail('must not invoke model')},{check:safeCheck});
  assert.equal(result.state,'failed');assert.equal(result.code,'CINEMATIC_DESIGN_UNAVAILABLE');
 }
});

test('Python trusted bridge sends one opaque native descriptor and skips alpha validation',()=>{
 const helper=fileURLToPath(new URL('./portrait-native.py',import.meta.url));
 const script=String.raw`
import runpy,sys,json,hashlib
run=runpy.run_path(sys.argv[1])["run"]
def digest(x): return hashlib.sha256(json.dumps(x,sort_keys=True).encode()).hexdigest()
class Runner:
 def generate(self,claim):
  d=claim["providerRequest"]; assert d["arguments"]["transparent_background"] is False
  prompt=d["arguments"]["prompt"]
  assert "2:1, 1536×768" in prompt and "directly on the 2:1 canvas" in prompt and "빈 마루" in prompt
  assert prompt.index("마른 붓의 굵은 먹선과 큰 여백") < prompt.index("[2 Exact shot request]")
  assert json.loads(prompt.split("[2 Exact shot request]\n",1)[1])["prompt"] == "저녁의 마당, 바람에 흔들리는 한 점의 불빛."
  assert "exclude-this-other-section" not in prompt
  raw=bytes.fromhex(fixture)
  return raw,{**claim,"sha256":hashlib.sha256(raw).hexdigest(),"actualToolOutput":True}
 def execution_evidence(self): return {"outcome":"joined"}
def prohibited(*a): raise AssertionError("must not invoke portrait/alpha")
from types import SimpleNamespace
import base64
run.__globals__["bind"]=lambda *_:(prohibited,lambda *_:(Runner(),SimpleNamespace(settled=True)),prohibited,None,None,digest,prohibited)
data = json.load(sys.stdin)
fixture = data.pop("fixture")
result=run(SimpleNamespace(stopped=SimpleNamespace(is_set=lambda:False)),
           {"mode":"render-cinematic-source","data":data},"config","pin")
assert result["code"]==0
sys.stdout.buffer.write(base64.b64decode(result["base64"]))
`;
 const p=spawnSync('/usr/bin/python3',['-B','-c',script,helper],{input:JSON.stringify({...input,design,fixture:png.toString('hex')})});
 assert.equal(p.status,0,p.stderr.toString());
 assert.deepEqual(p.stdout,png);
});

test('3:2 ImageGen result is not cropped or published as a new cinematic source',async t=>{
 const f=fixture(t);
 const pds={capability:async()=>true,readCinematicSource:async()=>input,readCinematicConcept:async()=>concept(),
  publishCinematicSource:async()=>assert.fail('wrong aspect must not publish')};
 const result=await imageTick({...f,mode:'run',pds,renderCinematicSource:async()=>backgroundPng},{check:safeCheck});
 assert.equal(result.state,'failed');
 assert.deepEqual(result.diagnostic,{stage:'validate-png',reason:'CINEMATIC_SOURCE_PNG_GEOMETRY_INVALID',width:1536,height:1024});
 assert.equal(f.state().result_ref,null);
});

test('bad original geometry records exact dimensions without publishing; lost publish ACK stays unresolved',async t=>{
 for(const bad of [true,false]){
  const f=fixture(t),raw=Buffer.from(png);
  if(bad)raw.writeUInt32BE(1824,16);
  let sends=0;
  const pds={capability:async()=>true,readCinematicSource:async()=>input,readCinematicConcept:async()=>concept(),
   publishCinematicSource:async()=>{sends++;throw Error('unconfirmed');}};
  const r=await imageTick({...f,mode:'run',pds,renderCinematicSource:async()=>raw},{check:safeCheck});
  assert.equal(f.state().portrait_read_owner_pid,null);
  assert.equal(sends,bad?0:2);
  assert.equal(r.state,bad?'failed':'requires_reconciliation');
  assert.equal(f.state().status,bad?'failed':'running');
  if(bad)assert.deepEqual(r.diagnostic,{stage:'validate-png',reason:'CINEMATIC_SOURCE_PNG_GEOMETRY_INVALID',width:1824,height:768});
  else assert.equal(r.diagnostic.stage,'publish');
 }
});

test('after publish transport loss, use fenced absence or an exact persisted result, never guess',async t=>{
 for(const scenario of ['absent','published','unexpected','unavailable']){
  await t.test(scenario,async t=>{
   const f=fixture(t);let rendered=0,sent=0,reads=0,canonicalArtifact;
   const pds={capability:async()=>true,readCinematicSource:async()=>input,
    readCinematicConcept:async()=>concept(),publishCinematicSource:async(_claim,artifact)=>{
     sent++;
     if(canonicalArtifact)assert.deepEqual(artifact,canonicalArtifact);
     canonicalArtifact=artifact;throw Error('DOCUMENT_WORKER_LOCAL_TRANSPORT');
    },
    reconcileCinematicSource:async claim=>{
     assert.equal(claim.claimStamp,f.state().updated_at);reads++;
     if(scenario==='absent')return {state:'absent_fenced'};
     if(scenario==='published'){
      if(reads===1)throw Error('PDS restarting');
      return {state:'published',resultRef};
     }
     if(scenario==='unexpected')return {state:'published',resultRef:'binari/cinematics/sources/changed.json'};
     return {};
    }};
   const opts={...f,officialRequestId,taskId,pds,renderCinematicSource:async()=>{rendered++;return png;},reconcileDelays:[0,0]};
   const {runCinematicSourceFromPath}=await import('./cinematic-source-worker.mjs');
   const result=await runCinematicSourceFromPath(opts);
   assert.equal(sent,2);assert.equal(rendered,1);
   if(scenario==='absent'){
    assert.equal(result.state,'failed');assert.equal(result.code,'CINEMATIC_SOURCE_PUBLICATION_ABSENT_FENCED');
    assert.equal(f.state().status,'failed');assert.equal(f.state().retry_count,0);assert.equal(f.state().result_ref,null);
    assert.equal((await runCinematicSourceFromPath(opts)).state,'not_claimable');
   }else if(scenario==='published'){
    assert.equal(reads,2);assert.equal(result.state,'completed');assert.equal(f.state().result_ref,resultRef);
   }else{
    assert.equal(result.state,'requires_reconciliation');assert.equal(f.state().status,'running');
   }
  });
 }
});

test('resident background lane combines typed scene, common design and background design before one render',async t=>{
 const input={title:'빈 마루',timeOfDay:'저녁',season:'가을',mood:'적막',color:'먹색',location:'한옥',requirements:'기둥을 분리',additionalNotes:'낮은 시점'};
 const f=fixture(t,input,true),order=[];
 const snapshot=concept();
 snapshot.document.hierarchy.groups[0].categoryIds.push(backgroundDesignId);
 snapshot.document.sections.push({id:backgroundDesignId,title:'배경 디자인 설계',fields:[{id:'content',value:'# 배경 디자인 설계\n\n빈 동선을 확보한다.'}]},
  {id:'expression',fields:[{id:'visualStyle',value:{schemaVersion:'ngs.character-style-contract.v1',visualStyle:'선명한 실루엣'}},
   {id:'visualProhibitions',value:{schemaVersion:'ngs.character-style-constraints.v1',visualProhibitions:'글자 없음'}}]});
 const resultRef='binari/cinematics/backgrounds/'+sha(backgroundPng)+'.json';
 const pds={capability:async(_signal,operation)=>operation===backgroundSourceType,
  readBackgroundSource:async()=>{order.push('inputs');return input;},
  readCinematicConcept:async()=>{order.push('concept');return snapshot;},
  publishBackgroundSource:async(_,artifact)=>{order.push('publish');assert.equal(artifact.sha256,sha(backgroundPng));return {ok:true,resultRef};}};
 const renderCinematicSource=async data=>{order.push('render');assert.ok(validBackgroundRenderInput(data));
  assert.match(data.prompt,/시간대: 저녁[\s\S]*필수 요청 사항: 기둥을 분리[\s\S]*추가 요청 사항: 낮은 시점/);
  assert.equal(data.design.categoryId,backgroundDesignId);
  assert.deepEqual(data.common.map(x=>x.id),['visualStyle','visualProhibitions']);return backgroundPng;};
 const out=await imageTick({...f,mode:'run',pds,renderCinematicSource},{check:safeCheck});
 assert.equal(out.state,'completed');assert.deepEqual(order,['inputs','concept','render','publish']);
 assert.equal(f.state().result_ref,resultRef);
});

const uiPngFixture=Buffer.concat([Buffer.from('89504e470d0a1a0a','hex'),
 chunk('IHDR',Buffer.concat([u32(8),u32(3),Buffer.from([8,6,0,0,0])])),
 chunk('IDAT',deflateSync(Buffer.alloc(3*(8*4+1)))),chunk('IEND',Buffer.alloc(0))]);
const uiDesignMarkdown='# UI 디자인\n\n한지와 먹글씨';
const uiDesign={categoryId:'category.add.4573d0a2e2c659fc0bba561ba5774ac8',markdown:uiDesignMarkdown,sha256:sha(uiDesignMarkdown)};
test('UI work uses existing image slot; manual never renders and generated uses only bound design; PDS alone completes',async t=>{
 for(const mode of ['generate','import']){
  const input={kind:'button',title:'들어가기',...(mode==='generate'?{prompt:'wide 3:1'}:{sha256:sha(uiPngFixture)})};
  const f=fixture(t,input,mode),events=[];
  const ref='binari/ui/assets/'+officialRequestId+'/manifest.json';
  const pds={
   capability:async(_signal,type)=>{assert.equal(type,'ui.asset.'+mode);return true;},
   readUiAsset:async(op,claim)=>{events.push('read');assert.equal(claim.claimStamp,f.state().updated_at);assert.equal(op,'ui.asset.'+mode);return {...input,...(mode==='generate'?{design:uiDesign}:{})};},
   publishUiAsset:async(_op,claim,artifact)=>{
    events.push('publish');
    assert.equal(f.state().status,'running');
    if(mode==='generate')assert.deepEqual(artifact,{sha256:sha(uiPngFixture),base64:uiPngFixture.toString('base64'),designSha256:uiDesign.sha256});
    else assert.equal(artifact,undefined);
    f.db.prepare("UPDATE workflow_tasks SET status='completed', result_ref=?,updated_at=?").run(ref,claim.claimStamp+1);
    return {ok:true,resultRef:ref};
   }};
  const result=await imageTick({...f,mode:'run',pds,renderUiAsset:async args=>{
   events.push('render');assert.deepEqual(args,{...input,design:uiDesign});return uiPngFixture;
  }},{check:safeCheck});
  assert.equal(result.state,'completed');assert.equal(f.state().result_ref,ref);
  assert.deepEqual(events,mode==='generate'?['read','render','publish']:['read','publish']);
 }
});
test('UI ambiguous handoff never lets the image worker own a reserved save',async t=>{
 const input={kind:'logo',title:'비나리',sha256:sha(uiPngFixture)};
 const f=fixture(t,input,'import');
 const pds={capability:async()=>true,readUiAsset:async()=>input,publishUiAsset:async()=>{
  f.db.exec("UPDATE workflow_tasks SET save_started_at=updated_at");
  throw Error('connection lost');
 }};
 const result=await imageTick({...f,mode:'run',pds},{check:safeCheck});
 assert.equal(result.state,'requires_reconciliation');
 assert.equal(f.state().status,'running');
});
test('UI native transparent descriptor uses dedicated artwork proportions without slice/stretch',()=>{
 const helper=fileURLToPath(new URL('./portrait-native.py',import.meta.url));
 const script=String.raw`
import runpy,sys,json,hashlib
from types import SimpleNamespace
run=runpy.run_path(sys.argv[1])["run"]
def digest(x): return hashlib.sha256(json.dumps(x,sort_keys=True).encode()).hexdigest()
class Runner:
 def generate(self,claim):
  args=claim["providerRequest"]["arguments"]
  assert args["transparent_background"] is True and "wide 3:1" in args["prompt"]
  assert "Do not stretch" in args["prompt"] and "nine-slice" in args["prompt"]
  assert "한지와 먹글씨" in args["prompt"] and "들어가기" in args["prompt"]
  raw=b'bridge-test-PNG-placeholder'
  return raw,{**claim,"sha256":hashlib.sha256(raw).hexdigest(),"actualToolOutput":True}
 def execution_evidence(self): return {"outcome":"joined"}
def prohibited(*a): raise AssertionError("UI must not invoke a scene, a reference, or portrait silhouette validation")
pairs=SimpleNamespace(descriptor=prohibited)
def factory(*a): raise AssertionError("wording must not use NPC portrait factory")
factory.ui_asset=lambda *_:(Runner(),SimpleNamespace(settled=True))
run.__globals__["bind"]=lambda *_:(factory,prohibited,prohibited,pairs,None,digest,prohibited)
r=run(SimpleNamespace(stopped=SimpleNamespace(is_set=lambda:False)),{"mode":"render-ui-asset","data":json.load(sys.stdin)},"config","pin")
assert r["code"]==0
`;
 const r=spawnSync('python3',['-c',script,helper],{input:JSON.stringify({kind:'button',title:'들어가기',prompt:'wide 3:1',design:uiDesign}),encoding:'utf8'});
 assert.equal(r.status,0,r.stderr);
});
