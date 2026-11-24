/** Exact formal prop derivative execution. No filesystem authority or global scan. */
import {DatabaseSync} from 'node:sqlite';
import {canonical,sha} from './vendor/planner-f24d23a/envelope.mjs';
import {exactProp,propValidator,propAnimationType,propTypes} from './vendor/prop-composite/prop-generate-envelope.mjs';
import {buildPropGeneratePlan,exactPropTask,isPropRef,propKeys,propRef} from './vendor/prop-composite/prop-generate-plan.mjs';
import {verifyPropMedia,propPackageMediaLimit} from './vendor/prop-composite/prop-package-contract.mjs';
import {assertDatabasePath,protectSchema,workflowTaskEligibleSql,workflowTaskRetrySetSql,markWorkflowTaskFailed} from './workflow-ready.mjs';

const roles={
 [propKeys.detail]:'detail',[propKeys.pixel]:'pixel',
 [propKeys.animDetail]:'detail',[propKeys.animPixel]:'pixel',
};
const check=(x,reason='PROP_DERIVATION_BINDING')=>{if(!x)throw Error(reason);};
const dimensions=(v,max)=>exactProp(v,['width','height'])&&['width','height'].every(k=>Number.isSafeInteger(v[k])&&v[k]>=12&&v[k]<=max);
const sign=Buffer.from('89504e470d0a1a0a','hex');
const MAX_INPUT=40*1024*1024, MAX_PIXELS=30_000_000;
const lane=k=>k===propKeys.animDetail||k===propKeys.animPixel;
const hex=h=>typeof h==='string'&&/^[a-f0-9]{64}$/u.test(h);
function tx(db,fn){
 db.exec('BEGIN IMMEDIATE');
 try{const out=fn();db.exec('COMMIT');return out;}catch(e){db.exec('ROLLBACK');throw e;}
}
function selected(db,taskId,officialRequestId,status,stamp){
 const claiming=status==='eligible';
 const row=db.prepare("SELECT * FROM workflow_runs WHERE official_request_id=? AND state='planned' AND reason IS NULL").get(officialRequestId);
 const task=db.prepare(`SELECT t.* FROM workflow_tasks t WHERE t.task_id=? AND t.official_request_id=? AND t.result_ref IS NULL${claiming?` AND ${workflowTaskEligibleSql('t')}`:''}`).get(taskId,officialRequestId);
 if(!row||!task||task.worker_type!=='postprocess'||!(Object.hasOwn(roles,task.task_key))||
    (!claiming&&(task.status!==status||task.updated_at!==stamp)))return null;
 check(Object.hasOwn(propValidator,row.request_type)&&row.project_id==='binari');
 const payload=JSON.parse(row.payload);
 check(payload.schemaVersion===row.schema_version&&propValidator[row.request_type](payload,row.project_id)===row.payload&&
    sha(row.payload)===row.payload_sha256&&Number.isSafeInteger(row.registered_at)&&row.registered_at>=0);
 const plan=buildPropGeneratePlan({officialRequestId,requestType:row.request_type,payload});
 const target=plan.find(x=>x.id===taskId),parent=plan.find(x=>x.id===target?.dependsOn);
 check(target&&parent&&exactPropTask(task,target)&&target.type===task.task_type);
 const read=key=>db.prepare('SELECT * FROM workflow_tasks WHERE task_id=? AND official_request_id=?').get(key,officialRequestId);
 const source=read(parent.id);
 if(!source||source.status!=='completed')return null;
 check(exactPropTask(source,parent)&&isPropRef(parent.key,source.result_ref));
 // For initial subtle generation and append alike, animation geometry
 // comes from static lanes. The initial optional animation may finish RGB
 // first; no claim/failed retry is consumed while static geometry isn't ready.
 const geometryKey=target.key===propKeys.animDetail?propKeys.detail:propKeys.pixel;
 let geometry=null;
 if(lane(target.key)&&row.request_type!==propAnimationType){
  const required=plan.find(x=>x.key===geometryKey);
  geometry=read(required.id);
  if(!geometry||geometry.status!=='completed')return null;
  check(exactPropTask(geometry,required)&&isPropRef(required.key,geometry.result_ref));
 }
 return {row,task,target,parent,source,geometry,role:roles[target.key],animated:lane(target.key)};
}
function imageDescriptor(row,animated){
 check(exactProp(row,['assetId','sha256','bytes','width','height','mediaType','frameDurationsMs'])&&
       hex(row.sha256)&&row.assetId===row.sha256+'.png'&&row.mediaType===(animated?'image/apng':'image/png')&&
       Number.isSafeInteger(row.bytes)&&row.bytes>=57&&row.bytes<=propPackageMediaLimit&&
       dimensions({width:row.width,height:row.height},2048),'PROP_DERIVATION_SOURCE_DESCRIPTOR');
 const d=row.frameDurationsMs;
 check(Array.isArray(d)&&d.length<=(animated?96:0)&&(!animated||d.length>=2),'PROP_DERIVATION_SOURCE_DESCRIPTOR');
 return row;
}
function timing(value,animated){
 check(Array.isArray(value)&&((!animated&&value.length===0)||(animated&&value.length>=2&&value.length<=96&&
       value.every(x=>Number.isSafeInteger(x)&&x>=10&&x<=1000)&&value.reduce((x,y)=>x+y,0)>=100&&
       value.reduce((x,y)=>x+y,0)<=10000)),'PROP_DERIVATION_TIMING');
 return value;
}
function assetBytes(raw,descriptor){
 check(Buffer.isBuffer(raw)&&raw.length===descriptor.bytes&&raw.length>=33&&sha(raw)===descriptor.sha256&&
       raw.subarray(0,8).equals(sign)&&raw.toString('ascii',12,16)==='IHDR'&&raw.readUInt32BE(16)===descriptor.width&&
       raw.readUInt32BE(20)===descriptor.height,'PROP_DERIVATION_INPUT_HASH');
 return raw;
}
function frozenRef(value,expected){
 check(exactProp(value,['taskId','resultRef'])&&value.taskId===expected.taskId&&value.resultRef===expected.resultRef,
       'PROP_DERIVATION_SOURCE_REF');
 return value;
}

export async function preparePropDerivation(pds,binding,{signal}={}){
 const {role,animated}=binding;
 const dto=await pds.readPropDerivation(binding,{signal});
 const mediaInput=!animated||role==='pixel';
 const geometryProof=animated?'geometry':null;
 check(exactProp(dto,['source',mediaInput?'media':'manifest','canvas',...(geometryProof?[geometryProof]:[])]));
 const source=frozenRef(dto.source,binding.source);
 const limit=role==='pixel'?512:2048;
 check(dimensions(dto.canvas,limit),'PROP_DERIVATION_CANVAS');
 let geometry=null;
 if(animated){
  geometry=dto.geometry;
  if(binding.geometry){
   check(exactProp(geometry,['taskId','resultRef','media']));
   frozenRef({taskId:geometry.taskId,resultRef:geometry.resultRef},binding.geometry);
   check(imageDescriptor(geometry.media,false).sha256===geometry.resultRef.slice(-64));
  }else{
   check(exactProp(geometry,['revision','sha256','media'])&&binding.package&&
     geometry.revision===binding.package.revision&&geometry.sha256===binding.package.sha256);
   imageDescriptor(geometry.media,false);
  }
  check(dto.canvas.width===geometry.media.width&&dto.canvas.height===geometry.media.height,'PROP_DERIVATION_CANVAS');
  // A matching digest alone does not bind caller-supplied dimensions: read
  // and verify the exact claimed static bytes before fixing the motion canvas.
  const fixed=assetBytes(await pds.fetchPropDerivationAsset(binding,geometry.media,{signal,purpose:'geometry'}),
                       geometry.media);
  await verifyPropMedia(fixed,geometry.media);
 }
 if(mediaInput){
  const descriptor=role==='pixel'?imageDescriptor(dto.media,animated):dto.media;
  if(role==='detail')check(exactProp(descriptor,['sha256','bytes','width','height','mediaType'])&&descriptor.mediaType==='image/png'&&
    hex(descriptor.sha256)&&Number.isSafeInteger(descriptor.bytes)&&descriptor.bytes>=57&&descriptor.bytes<=propPackageMediaLimit&&
    dimensions({width:descriptor.width,height:descriptor.height},2048),'PROP_DERIVATION_SOURCE_DESCRIPTOR');
  check(descriptor.sha256===source.resultRef.slice(-64),'PROP_DERIVATION_SOURCE_REF');
  const frameDurationsMs=timing(role==='pixel'?descriptor.frameDurationsMs:[],animated);
  check(dto.canvas.width*dto.canvas.height*(frameDurationsMs.length||1)<=MAX_PIXELS,'PROP_DERIVATION_OUTPUT_BUDGET');
  if(role==='pixel')check(dto.canvas.width<=descriptor.width&&dto.canvas.height<=descriptor.height&&
    dto.canvas.width*dto.canvas.height<descriptor.width*descriptor.height&&
    Math.abs(dto.canvas.width*descriptor.height-dto.canvas.height*descriptor.width)<=Math.max(descriptor.width,descriptor.height),
    'PROP_DERIVATION_PIXEL_SCALE');
  else check(dto.canvas.width===descriptor.width&&dto.canvas.height===descriptor.height,'PROP_DERIVATION_CANVAS');
  const raw=assetBytes(await pds.fetchPropDerivationAsset(binding,descriptor,{signal}),descriptor);
  if(role==='pixel')await verifyPropMedia(raw,descriptor);
  return {source,geometry,canvas:dto.canvas,frameDurationsMs,media:descriptor,buffers:[raw]};
 }
 const manifest=dto.manifest;
 check(exactProp(manifest,['schemaVersion','officialRequestId','taskId','propId','width','height','frameDurationsMs','frames'])&&
    manifest.schemaVersion==='ngs.prop-native-rgb-sequence.v1'&&manifest.officialRequestId===binding.claim.officialRequestId&&
    manifest.taskId===binding.source.taskId&&manifest.propId===binding.propId&&
    dimensions({width:manifest.width,height:manifest.height},2048)&&
    sha(canonical(manifest))===source.resultRef.slice(-64),'PROP_DERIVATION_RGB_MANIFEST');
 const d=timing(manifest.frameDurationsMs,true);
 check(Array.isArray(manifest.frames)&&manifest.frames.length===d.length&&manifest.width*manifest.height*d.length<=MAX_PIXELS&&
       dto.canvas.width*dto.canvas.height*d.length<=MAX_PIXELS,'PROP_DERIVATION_INPUT_BUDGET');
 check(manifest.frames.every((f,i)=>exactProp(f,['index','sha256','bytes','mediaType'])&&f.index===i&&hex(f.sha256)&&
       f.mediaType==='image/png'&&Number.isSafeInteger(f.bytes)&&f.bytes>=57&&f.bytes<=propPackageMediaLimit)&&
       manifest.frames.reduce((n,f)=>n+f.bytes,0)<=MAX_INPUT,'PROP_DERIVATION_RGB_FRAMES');
 const frames=manifest.frames.map(f=>({...f,width:manifest.width,height:manifest.height}));
 const buffers=[];
 for(const f of frames){
  signal?.throwIfAborted();
  buffers.push(assetBytes(await pds.fetchPropDerivationAsset(binding,f,{signal}),f));
 }
 return {source,geometry,canvas:dto.canvas,frameDurationsMs:d,frames,buffers};
}

export function nextPropDerivationFromPath(dbPath){
 assertDatabasePath(dbPath);
 const db=new DatabaseSync(dbPath,{readOnly:true});
 try{
  protectSchema(db);
  // Poll only the deterministic derivative families. Full exact row and
  // canonical payload validation happens again in the claim transaction.
  const found=db.prepare(`SELECT t.task_id AS taskId,t.official_request_id AS officialRequestId
    FROM workflow_tasks t JOIN workflow_runs r USING(official_request_id)
    JOIN workflow_tasks p ON p.task_id=t.depends_on_task_id AND p.official_request_id=t.official_request_id
    WHERE r.state='planned' AND r.reason IS NULL AND r.project_id='binari'
      AND r.request_type IN (${propTypes.map(()=>'?')})
      AND ${workflowTaskEligibleSql('t')} AND t.result_ref IS NULL AND t.worker_type='postprocess'
      AND t.task_key IN (${Object.keys(roles).map(()=>'?')}) AND p.status='completed'
      AND (r.request_type=? OR t.task_key NOT IN (?,?) OR EXISTS (
        SELECT 1 FROM workflow_tasks s WHERE s.official_request_id=t.official_request_id AND s.status='completed'
        AND s.task_key=CASE WHEN t.task_key=? THEN ? ELSE ? END))
    ORDER BY r.registered_at,r.official_request_id,t.task_key LIMIT 1`)
    .get(...propTypes,...Object.keys(roles),propAnimationType,propKeys.animDetail,propKeys.animPixel,
         propKeys.animDetail,propKeys.detail,propKeys.pixel);
  return found?{taskId:found.taskId,officialRequestId:found.officialRequestId}:null;
 }finally{db.close();}
}

export async function runPropDerivationFromPath({dbPath,officialRequestId,taskId,pds,render,signal}){
 check(typeof officialRequestId==='string'&&officialRequestId.length===81&&/^request\.official\.[a-f0-9]{64}$/u.test(officialRequestId)&&
       typeof taskId==='string'&&taskId.length===78&&/^workflow\.task\.[a-f0-9]{64}$/u.test(taskId));
 check(['readPropDerivation','fetchPropDerivationAsset','stagePropDerivation'].every(k=>typeof pds?.[k]==='function')&&
       typeof render==='function','PROP_DERIVATION_PORTS');
 assertDatabasePath(dbPath);
 const db=new DatabaseSync(dbPath);
 try{
  db.exec('PRAGMA foreign_keys=ON; PRAGMA busy_timeout=5000;');protectSchema(db);
  if(signal?.aborted)return {state:'not_claimable',taskId};
  const attempt=tx(db,()=>{
   const x=selected(db,taskId,officialRequestId,'eligible');
   if(!x)return null;
   const stamp=Math.max(Date.now(),x.task.updated_at+1);
   check(Number.isSafeInteger(stamp));
   check(db.prepare(`UPDATE workflow_tasks SET status='running',updated_at=?,${workflowTaskRetrySetSql}
      WHERE task_id=? AND official_request_id=? AND ${workflowTaskEligibleSql()} AND result_ref IS NULL AND updated_at=?`)
    .run(stamp,taskId,officialRequestId,x.task.updated_at).changes===1);
   const ref=r=>Object.freeze({taskId:r.task_id,resultRef:r.result_ref});
   return {x,binding:Object.freeze({claim:Object.freeze({officialRequestId,taskId,projectId:x.row.project_id,
    payloadSha256:x.row.payload_sha256,claimStamp:stamp}),propId:x.target.input.propId,key:x.target.key,
    role:x.role,animated:x.animated,source:ref(x.source),geometry:x.geometry?ref(x.geometry):null,
    package:x.target.input.source?Object.freeze({...x.target.input.source}):null})};
  });
  if(!attempt)return {state:'not_claimable',taskId};
  const {x,binding}=attempt;
  const stamp=binding.claim.claimStamp;
  const exactCurrent=()=>{
   const current=selected(db,taskId,officialRequestId,'running',stamp);
   check(current&&['row','source','geometry'].every(k=>canonical(current[k])===canonical(x[k])),'PROP_DERIVATION_STALE');
  };
  let sent=false;
  try{
   const job=await preparePropDerivation(pds,binding,{signal});
   const raw=await render(job,binding,{signal});
   check(Buffer.isBuffer(raw)&&raw.length<=propPackageMediaLimit,'PROP_DERIVATION_OUTPUT_BUDGET');
   const hash=sha(raw);
   const media={...job.canvas,sha256:hash,assetId:hash+'.png',bytes:raw.length,
     mediaType:binding.animated?'image/apng':'image/png',frameDurationsMs:job.frameDurationsMs};
   await verifyPropMedia(raw,media);
   signal?.throwIfAborted();
   // Recheck the live claim and prerequisite proofs before any stage write;
   // the PDS port repeats this authorization independently when committing.
   tx(db,exactCurrent);
   sent=true;
   const reply=await pds.stagePropDerivation(binding,{source:job.source,geometry:job.geometry,media,bytes:raw},{signal});
   const resultRef=propRef(binding.key,hash);
   check(exactProp(reply,['ok','resultRef'])&&reply.ok===true&&reply.resultRef===resultRef,'PROP_DERIVATION_ACK');
   tx(db,()=>{
    exactCurrent();
    check(db.prepare(`UPDATE workflow_tasks SET status='completed',result_ref=?,updated_at=?
     WHERE task_id=? AND official_request_id=? AND status='running' AND updated_at=? AND result_ref IS NULL`)
     .run(resultRef,Math.max(Date.now(),stamp+1),taskId,officialRequestId,stamp).changes===1);
   });
   return {state:'completed',taskId,resultRef};
  }catch{
   if(!sent)try{
    tx(db,()=>{exactCurrent();check(markWorkflowTaskFailed(db,{...binding.claim,workerType:'postprocess'}));});
    return {state:'failed',taskId,code:'PROP_DERIVATION_READ_OR_RENDER_FAILED'};
   }catch{}
   return {state:'requires_reconciliation',taskId,code:'PROP_DERIVATION_PUBLICATION_UNCERTAIN'};
  }
 }finally{db.close();}
}
