/** CPU-only postprocessing for the formal non-NPC motion preset workflow. */
import {DatabaseSync} from 'node:sqlite';
import {mkdir,mkdtemp,rm,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {assertDatabasePath,protectSchema,markWorkflowTaskFailed,workflowTaskEligibleSql,workflowTaskRetrySetSql} from './workflow-ready.mjs';
import {directions,sha256,digestPattern,need,validTiming,pngShape,mp4Shape,width,height,maxBytes,encodePresetVideo} from './preset-video-artifact.mjs';
import {DefinitePresetWriteRejection} from './preset-video-ports.mjs';

export const requestType='motion-preset.convert-2d',requestSchema='ngs.motion-preset-convert-2d.v1';
export const framesType=requestType+'.frames',encodeType=requestType+'.encode',finalType=requestType+'.finalize';
export const framesKey=d=>'motion_2d_frames_'+d,encodeKey=d=>'motion_2d_encode_'+d,finalKey='motion_2d_finalize';
const encodeKeys=directions.map(encodeKey),worker='postprocess',project='binari';
const official=/^request\.official\.[a-f0-9]{64}$/u;
const sourceId=/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/u,variantId=/^[a-z][a-z0-9-]{1,63}$/u;
const finalResult=/^motion\.2d\.published\.[a-f0-9]{64}$/u;
const slots=encodeKeys.map(()=>'?').join(',');
const dict=v=>v!==null&&typeof v==='object'&&!Array.isArray(v);
const exact=(v,ks)=>dict(v)&&Object.keys(v).length===ks.length&&ks.every(k=>Object.hasOwn(v,k));
const canonical=v=>Array.isArray(v)?'['+v.map(canonical).join(',')+']':
 dict(v)?'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+canonical(v[k])).join(',')+'}':JSON.stringify(v);
export const motionTaskId=(id,key)=>'workflow.task.'+sha256(canonical([id,key]));
const refHash=(v,prefix)=>typeof v==='string'&&v.startsWith(prefix)&&digestPattern.test(v.slice(prefix.length))?v.slice(prefix.length):null;
const validPayload=p=>exact(p,['schemaVersion','projectId','sourcePresetId','variantPresetId','label'])&&
 p.schemaVersion===requestSchema&&p.projectId===project&&typeof p.sourcePresetId==='string'&&sourceId.test(p.sourcePresetId)&&
 typeof p.variantPresetId==='string'&&variantId.test(p.variantPresetId)&&p.sourcePresetId!==p.variantPresetId&&
 typeof p.label==='string'&&p.label.isWellFormed()&&p.label===p.label.trim()&&p.label.length>0&&[...p.label].length<=120&&
 !/[\p{Cc}\u2028\u2029]/u.test(p.label);
const args=(p,d)=>({sourcePresetId:p.sourcePresetId,variantPresetId:p.variantPresetId,...d});
const runWhere=`r.state='planned' AND r.reason IS NULL AND r.request_type=? AND r.schema_version=? AND r.project_id=?`;

export function choosePresetPostprocess(dbPath){
 assertDatabasePath(dbPath);
 const db=new DatabaseSync(dbPath,{readOnly:true});
 try{
  protectSchema(db);
  return db.prepare(`SELECT t.task_id AS taskId,t.official_request_id AS officialRequestId,t.task_key AS key,t.task_type AS type
   FROM workflow_tasks t JOIN workflow_runs r USING(official_request_id)
   WHERE ${runWhere} AND t.worker_type=? AND ${workflowTaskEligibleSql('t')} AND t.result_ref IS NULL
    AND ((t.task_type=? AND t.task_key=? AND t.depends_on_task_id IS NULL AND
      (SELECT COUNT(*) FROM workflow_tasks p WHERE p.official_request_id=t.official_request_id
       AND p.task_type=? AND p.worker_type=? AND p.status='completed' AND p.result_ref IS NOT NULL
       AND p.task_key IN (${slots}))=8)
     OR (t.task_type=? AND t.task_key IN (${slots}) AND EXISTS (SELECT 1 FROM workflow_tasks p
       WHERE p.task_id=t.depends_on_task_id AND p.official_request_id=t.official_request_id
       AND p.task_type=? AND p.worker_type='animation' AND p.status='completed' AND p.result_ref IS NOT NULL
       AND p.task_key=REPLACE(t.task_key,'motion_2d_encode_','motion_2d_frames_'))))
   ORDER BY CASE WHEN t.task_key=? THEN 0 ELSE 1 END,r.registered_at,t.official_request_id,t.task_key LIMIT 1`)
   .get(requestType,requestSchema,project,worker,finalType,finalKey,encodeType,worker,...encodeKeys,
        encodeType,...encodeKeys,framesType,finalKey)??null;
 }finally{db.close();}
}

function transaction(db,f){
 db.exec('BEGIN IMMEDIATE');
 try{const v=f();db.exec('COMMIT');return v;}catch(e){db.exec('ROLLBACK');throw e;}
}
const rowFor=(db,id,key)=>db.prepare(`SELECT task_id,task_key,task_type,worker_type,status,task_input,depends_on_task_id,result_ref
 FROM workflow_tasks WHERE official_request_id=? AND task_id=?`).get(id,motionTaskId(id,key));

function framesProof(db,id,p,d){
 const row=rowFor(db,id,framesKey(d));
 return row&&row.status==='completed'&&row.task_key===framesKey(d)&&row.task_type===framesType&&
  row.worker_type==='animation'&&row.task_input===canonical(args(p,{direction:d}))&&
  row.depends_on_task_id===motionTaskId(id,'motion_2d_image')?
  refHash(row.result_ref,'motion.2d.frames.'+d+'.'):null;
}
function finalProofs(db,id,p){
 const refs=[];
 for(const d of directions){
  const row=rowFor(db,id,encodeKey(d));
  if(!row||row.status!=='completed'||row.worker_type!==worker||row.task_key!==encodeKey(d)||row.task_type!==encodeType||
     row.task_input!==canonical(args(p,{direction:d}))||row.depends_on_task_id!==motionTaskId(id,framesKey(d))||
     !refHash(row.result_ref,'motion.2d.clip.'+d+'.')||!framesProof(db,id,p,d))return null;
  refs.push(row.result_ref);
 }
 return refs;
}

function claimTask(db,t){
 const {officialRequestId:id,taskId,key,type}=t;
 need(typeof id==='string'&&official.test(id)&&typeof key==='string'&&taskId===motionTaskId(id,key),'TASK_BINDING');
 const final=type===finalType&&key===finalKey,d=type===encodeType?directions.find(v=>encodeKey(v)===key):undefined;
 need(final||d!==undefined,'TASK_KIND');
 return transaction(db,()=>{
  const row=db.prepare(`SELECT t.updated_at,t.status,t.task_input,t.depends_on_task_id,r.payload,r.payload_sha256,r.registered_at
   FROM workflow_tasks t JOIN workflow_runs r USING(official_request_id)
   WHERE ${runWhere} AND t.worker_type=? AND t.task_type=? AND t.task_key=? AND t.task_id=? AND t.official_request_id=?
   AND ${workflowTaskEligibleSql('t')} AND t.result_ref IS NULL`)
   .get(requestType,requestSchema,project,worker,type,key,taskId,id);
  if(!row)return null;
  const p=JSON.parse(row.payload);
  need(validPayload(p)&&row.payload===canonical(p)&&row.payload_sha256===sha256(row.payload),'PAYLOAD_BINDING');
  const parent=final?null:motionTaskId(id,framesKey(d));
  const extra=final?{dependsOnTaskIds:directions.map(v=>motionTaskId(id,encodeKey(v)))}:{direction:d};
  need(row.depends_on_task_id===parent&&row.task_input===canonical(args(p,extra)),'TASK_INPUT');
  const proof=final?finalProofs(db,id,p):framesProof(db,id,p,d);
  if(!proof)return null;
  const stamp=Math.max(Date.now(),row.updated_at+1);
  need(Number.isSafeInteger(row.updated_at)&&row.updated_at>=0&&Number.isSafeInteger(stamp)&&stamp<Number.MAX_SAFE_INTEGER,'CLAIM_STAMP');
  const changed=db.prepare(`UPDATE workflow_tasks AS t SET status='running',updated_at=?,${workflowTaskRetrySetSql}
   WHERE t.task_id=? AND t.official_request_id=? AND t.status=? AND t.updated_at=? AND t.result_ref IS NULL
   AND ${workflowTaskEligibleSql('t')}`).run(stamp,taskId,id,row.status,row.updated_at).changes;
  need(changed===1,'CLAIM_CHANGED');
  return {claim:{officialRequestId:id,taskId,projectId:project,payloadSha256:row.payload_sha256,claimStamp:stamp},
   row,p,d,final,proof};
 });
}

export function decodeFrameBundle(raw,state){
 const maxHeader=128*1024,maxFrames=64*1024*1024;
 need(Buffer.isBuffer(raw)&&raw.length>=5&&raw.length<=4+maxHeader+maxFrames,'FRAME_BYTES');
 const size=raw.readUInt32BE(0),end=4+size;
 need(size>0&&size<=maxHeader&&end<raw.length&&raw.length-end<=maxFrames,'FRAME_HEADER');
 const v=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(raw.subarray(4,end)));
 const timing={fps:v?.fps,frameCount:v?.frames?.length};
 need(exact(v,['direction','sourcePresetHash','fps','width','height','frames'])&&v.direction===state.d&&
  typeof v.sourcePresetHash==='string'&&digestPattern.test(v.sourcePresetHash)&&v.width===width&&v.height===height&&
  Array.isArray(v.frames)&&validTiming(timing),'FRAME_MANIFEST');
 need(v.frames.every(f=>exact(f,['bytes','sha256'])&&Number.isSafeInteger(f.bytes)&&f.bytes>=33&&
  f.bytes<=8*1024*1024&&typeof f.sha256==='string'&&digestPattern.test(f.sha256)),'FRAME_ENTRY');
 const {fps,...bound}=v;
 need(sha256(canonical(bound))===state.proof,'FRAME_PROOF');
 let offset=end;
 const frames=[];
 for(const f of v.frames){
  need(offset+f.bytes<=raw.length,'FRAME_LENGTH');
  const bytes=raw.subarray(offset,offset+f.bytes);offset+=f.bytes;
  need(pngShape(bytes)&&bytes.readUInt32BE(16)===width&&bytes.readUInt32BE(20)===height&&
   sha256(bytes)===f.sha256,'FRAME_HASH');
  frames.push(bytes);
 }
 need(offset===raw.length,'FRAME_LENGTH');
 return {timing,frames};
}

function exactEncoded(output,timing){
 need(exact(output,['mimeType','sha256','bytes','base64','fps','frameCount','width','height'])&&
  output.mimeType==='video/mp4'&&digestPattern.test(output.sha256)&&Number.isSafeInteger(output.bytes)&&
  output.bytes>=24&&output.bytes<=maxBytes&&typeof output.base64==='string'&&
  output.base64.length===4*Math.ceil(output.bytes/3)&&output.fps===timing.fps&&output.frameCount===timing.frameCount&&
  output.width===width&&output.height===height,'ENCODE_RESULT');
 const data=Buffer.from(output.base64,'base64');
 need(data.length===output.bytes&&data.toString('base64')===output.base64&&mp4Shape(data)&&sha256(data)===output.sha256,'ENCODE_BYTES');
 return {base64:output.base64,sha256:output.sha256};
}

function checkProof(db,s){
 const id=s.claim.officialRequestId;
 return s.final?canonical(finalProofs(db,id,s.p))===canonical(s.proof):framesProof(db,id,s.p,s.d)===s.proof;
}
function complete(db,t,s,ref){
 return transaction(db,()=>{
  need(checkProof(db,s),'COMPLETE_PROOF');
  const done=db.prepare(`UPDATE workflow_tasks SET status='completed',result_ref=?,updated_at=?
   WHERE task_id=? AND official_request_id=? AND status='running' AND updated_at=? AND result_ref IS NULL
   AND task_type=? AND task_key=? AND worker_type=? AND task_input=? AND depends_on_task_id IS ?
   AND EXISTS (SELECT 1 FROM workflow_runs r WHERE r.official_request_id=workflow_tasks.official_request_id
   AND ${runWhere} AND r.payload=? AND r.payload_sha256=? AND r.registered_at=?)`)
   .run(ref,Math.max(Date.now(),s.claim.claimStamp+1),t.taskId,t.officialRequestId,s.claim.claimStamp,t.type,t.key,worker,
    s.row.task_input,s.row.depends_on_task_id,requestType,requestSchema,project,s.row.payload,s.row.payload_sha256,s.row.registered_at);
  need(done.changes===1,'COMPLETE_UPDATE');
 });
}

export async function executePresetPostprocess(db,t,signal,{pds,encode=encodePresetVideo}){
 signal=signal??new AbortController().signal;
 if(signal.aborted)return {state:'not_claimable',taskId:t.taskId};
 const s=claimTask(db,t);
 if(!s)return {state:'not_claimable',taskId:t.taskId};
 let phase='read',dir;
 try{
  signal.throwIfAborted();
  let artifact={};
  if(!s.final){
   const {timing,frames}=decodeFrameBundle(await pds.readFrames(s.claim,signal),s);
   phase='encode';
   dir=await mkdtemp(join(tmpdir(),'workflow-preset-2d-encode-'));
   await mkdir(join(dir,'frames'),{mode:0o700});
   for(const [i,bytes] of frames.entries()){
    signal.throwIfAborted();
    await writeFile(join(dir,'frames',String(i).padStart(5,'0')+'.png'),bytes,{mode:0o600,flag:'wx'});
   }
   artifact=exactEncoded(await encode(dir,timing,signal),timing);
  }
  signal.throwIfAborted();
  need(checkProof(db,s),'PUBLISH_PROOF');
  phase='publish';
  const receipt=await pds.write(s.claim,artifact);
  const expected=s.final?null:'motion.2d.clip.'+s.d+'.'+artifact.sha256;
  need(exact(receipt,['ok','resultRef'])&&receipt.ok===true&&typeof receipt.resultRef==='string'&&
   (s.final?finalResult.test(receipt.resultRef):receipt.resultRef===expected),'PUBLICATION_UNCONFIRMED');
  phase='commit';
  complete(db,t,s,receipt.resultRef);
  return {state:'completed',taskId:t.taskId,resultRef:receipt.resultRef};
 }catch(e){
  const code=typeof e?.message==='string'&&/^PRESET_2D_[A-Z0-9_]{1,90}$/.test(e.message)?e.message:'PRESET_2D_UNCONFIRMED';
  let failed=false;
  if(!['publish','commit'].includes(phase)||phase==='publish'&&e instanceof DefinitePresetWriteRejection)
   try{failed=markWorkflowTaskFailed(db,{...s.claim,workerType:worker});}catch{}
  return {state:failed?'failed':'requires_reconciliation',taskId:t.taskId,stage:phase,code};
 }finally{
  if(dir)try{await rm(dir,{recursive:true,force:true});}catch{}
 }
}

export async function runPresetPostprocessFromPath({dbPath,signal,pds,encode,...target}){
 assertDatabasePath(dbPath);
 const db=new DatabaseSync(dbPath);
 try{db.exec('PRAGMA foreign_keys=ON; PRAGMA busy_timeout=5000;');protectSchema(db);
  return await executePresetPostprocess(db,target,signal,{pds,encode});
 }finally{db.close();}
}
