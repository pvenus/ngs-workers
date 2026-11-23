import {createHash} from 'node:crypto';
import {DatabaseSync} from 'node:sqlite';
import {buildPlan,exact} from './vendor/planner/planner.mjs';
import {canonical} from './vendor/planner/envelope.mjs';
import {audioKind,audioRequestType,audioTaskKey} from './vendor/planner/audio-generate-envelope.mjs';
import {assertDatabasePath,protectSchema,markWorkflowTaskFailed,workflowTaskEligibleSql,workflowTaskRetrySetSql} from './workflow-ready.mjs';
import {validateCaption,validateGeneratedLyrics} from './bgm-caption.mjs';
import {validateWavWithDuration} from './audio-wav.mjs';

const WORKER='audio';
const taskPattern=/^workflow\.task\.[a-f0-9]{64}$/u;
const requestPattern=/^request\.official\.[a-f0-9]{64}$/u;
const shaPattern=/^[a-f0-9]{64}$/u;
const sourceText=(value,max)=>typeof value==='string'&&value.isWellFormed()&&value.trim().length>0&&
 Buffer.byteLength(value,'utf8')<=max&&!value.includes('\x00');
const check=(pass,code)=>{if(!pass)throw Error(code);};
const cancelled=signal=>check(!signal?.aborted,'AUDIO_CANCELLED');
export class AudioDefinitelyRejected extends Error{}
export function nextAudioFromPath(dbPath){
 assertDatabasePath(dbPath);
 const db=new DatabaseSync(dbPath,{readOnly:true});
 try{
  protectSchema(db);
  return db.prepare(`SELECT t.task_id AS taskId,t.official_request_id AS officialRequestId
   FROM workflow_tasks t JOIN workflow_runs r USING(official_request_id)
   WHERE r.state='planned' AND ${workflowTaskEligibleSql('t')} AND t.result_ref IS NULL
    AND t.worker_type=? AND t.task_type=? AND t.task_key=? AND t.depends_on_task_id IS NULL
    AND (CASE WHEN json_valid(t.task_input) THEN json_extract(t.task_input,'$.kind') END)=?
   ORDER BY r.registered_at,t.official_request_id,t.task_id LIMIT 1`).get(WORKER,audioRequestType,audioTaskKey,audioKind)??null;
 }finally{db.close();}
}
export function audioResultRef(projectId,officialRequestId){
 check(projectId==='binari'&&requestPattern.test(officialRequestId),'AUDIO_RESULT_SCOPE');
 return 'canonical-files/'+projectId+'/audio/'+officialRequestId+'/metadata.json';
}
function tx(db,fn){
 db.exec('BEGIN IMMEDIATE');
 try{const r=fn();db.exec('COMMIT');return r;}
 catch(e){try{db.exec('ROLLBACK');}catch{}throw e;}
}
function selected(db,taskId,requestId,state,stamp){
 const eligible=state==='eligible';
 const row=db.prepare("SELECT * FROM workflow_runs WHERE official_request_id=? AND state='planned'").get(requestId);
 const task=db.prepare(`SELECT t.* FROM workflow_tasks t WHERE task_id=? AND official_request_id=?${eligible?` AND ${workflowTaskEligibleSql('t')}`:''}`).get(taskId,requestId);
 if(!row||!task||(!eligible&&task.status!==state)||(stamp!==undefined&&task.updated_at!==stamp)||
  task.worker_type!==WORKER||task.task_type!==audioRequestType||task.task_key!==audioTaskKey||task.result_ref!==null||task.depends_on_task_id!==null)return null;
 const payload=JSON.parse(row.payload);
 const plan=buildPlan({officialRequestId:requestId,projectId:row.project_id,requestType:row.request_type,
  schemaVersion:row.schema_version,payload,payloadSha256:row.payload_sha256,status:'registered',registeredAt:row.registered_at});
 const [target,...others]=plan.tasks;
 check(others.length===0&&target?.id===taskId&&target.type===audioRequestType&&target.key===audioTaskKey&&
  target.workerType===WORKER&&target.dependsOn===null&&canonical(target.input)===task.task_input&&
  plan.payload===row.payload,'AUDIO_TASK_BINDING_INVALID');
 return {row,task,payload};
}
function sameClaim(db,claim,found){
 const current=selected(db,claim.taskId,claim.officialRequestId,'running',claim.claimStamp);
 check(current&&canonical(current.row)===canonical(found.row),'AUDIO_CLAIM_CHANGED');
}
function captionInput(payload,source){
 check(exact(source,['conceptId','sectionSha256','title','content'])&&source.conceptId===payload.conceptId&&
  shaPattern.test(source.sectionSha256)&&sourceText(source.title,600)&&sourceText(source.content,256*1024),'AUDIO_MASTER_SOURCE_INVALID');
 if(payload.conceptId==='direct')check(source.title==='직접 입력'&&source.content===payload.conceptText&&
  source.sectionSha256===createHash('sha256').update(payload.conceptText,'utf8').digest('hex'),'AUDIO_MASTER_SOURCE_INVALID');
 return Object.freeze({masterConcept:{title:source.title,content:source.content},
  request:{title:payload.title,brief:payload.brief,durationSeconds:payload.durationSeconds,
   vocalMode:payload.text||payload.lyricsRequest?'use supplied vocal text only':'instrumental'}});
}
export async function runAudioFromPath({dbPath,taskId,officialRequestId,pds,adapters,composeCaption,composeLyrics,signal}){
 check(typeof taskId==='string'&&taskPattern.test(taskId)&&typeof officialRequestId==='string'&&requestPattern.test(officialRequestId),'AUDIO_IDS_REQUIRED');
 check(typeof pds?.read==='function'&&typeof pds?.publish==='function'&&typeof composeCaption==='function'&&adapters&&typeof adapters==='object'&&
  Object.keys(adapters).every(k=>k===audioKind&&typeof adapters[k]==='function'),'AUDIO_PORTS_REQUIRED');
 assertDatabasePath(dbPath);
 const db=new DatabaseSync(dbPath);
 try{
  db.exec('PRAGMA foreign_keys=ON; PRAGMA busy_timeout=5000;');protectSchema(db);
  if(signal?.aborted)return {state:'stopping'};
  const admitted=tx(db,()=>{
   const found=selected(db,taskId,officialRequestId,'eligible');
   if(!found)return null;
   const {kind}=found.payload;
   if(!Object.hasOwn(adapters,kind))return {unavailable:true,kind};
   const claimStamp=Math.max(Date.now(),found.task.updated_at+1);
   check(Number.isSafeInteger(claimStamp)&&claimStamp<Number.MAX_SAFE_INTEGER,'AUDIO_CLAIM_STAMP_INVALID');
   check(db.prepare(`UPDATE workflow_tasks AS t SET status='running',updated_at=?, ${workflowTaskRetrySetSql}
    WHERE task_id=? AND official_request_id=? AND ${workflowTaskEligibleSql('t')} AND result_ref IS NULL AND updated_at=?`)
    .run(claimStamp,taskId,officialRequestId,found.task.updated_at).changes===1,'AUDIO_CLAIM_CHANGED');
   return {found,claim:Object.freeze({officialRequestId,taskId,projectId:found.row.project_id,payloadSha256:found.row.payload_sha256,claimStamp})};
  });
  if(!admitted)return {state:'not_claimable',taskId};
  if(admitted.unavailable)return {state:'provider_unavailable',code:'AUDIO_PROVIDER_UNAVAILABLE',taskId,kind:admitted.kind};
  const {found,claim}=admitted;
  let publishing=false,code='AUDIO_MASTER_READ_FAILED';
  try{
   cancelled(signal);
   const source=await pds.read(claim,{signal});
   const basis=captionInput(found.payload,source);
   const {kind,title,durationSeconds,text,targetDurationSeconds,lyricsRequest}=found.payload;
   let lyrics=text;
   if(lyricsRequest!==undefined){
    code='AUDIO_LYRIC_FAILED';cancelled(signal);
    check(typeof composeLyrics==='function','AUDIO_LYRIC_UNAVAILABLE');
    lyrics=validateGeneratedLyrics(await composeLyrics({...basis,lyricsRequest},{signal}));
   }
   code='AUDIO_CAPTION_FAILED';cancelled(signal);
   const prompt=validateCaption(await composeCaption(basis,{signal}));
   const input=Object.freeze({kind,title,prompt,durationSeconds,...(lyrics?{text:lyrics}:{}),...(targetDurationSeconds===undefined?{}:{targetDurationSeconds})});
   code='AUDIO_RENDER_FAILED';cancelled(signal);
   const outcome=await adapters.bgm(input,{signal});
   code='AUDIO_MODEL_METADATA_INVALID';cancelled(signal);
   check(exact(outcome,['wav','bpm','bpmBasis'])&&((outcome.bpm===null&&outcome.bpmBasis==='unavailable')||
    (Number.isSafeInteger(outcome.bpm)&&outcome.bpm>=30&&outcome.bpm<=300&&outcome.bpmBasis==='model')),code);
   code='AUDIO_WAV_INVALID';
   const verified=validateWavWithDuration(outcome.wav,input.kind,found.payload.durationSeconds);
   if(targetDurationSeconds!==undefined&&verified.duration<targetDurationSeconds){
    code='AUDIO_FULL_DURATION_NOT_MET';
    throw Error(code);
   }
   const artifact=Object.freeze({...verified.artifact,bpm:outcome.bpm});
   const sourceBinding=Object.freeze({conceptId:source.conceptId,sectionSha256:source.sectionSha256});
   const resultRef=audioResultRef(claim.projectId,claim.officialRequestId);
   code='AUDIO_CLAIM_CHANGED';sameClaim(db,claim,found);
   publishing=true;
   const r=await pds.publish(claim,sourceBinding,artifact); // no interrupt after sending
   check(exact(r,['ok','resultRef'])&&r.ok===true&&r.resultRef===resultRef,'AUDIO_PUBLICATION_UNVERIFIED');
   tx(db,()=>{
    sameClaim(db,claim,found);
    check(db.prepare("UPDATE workflow_tasks SET status='completed',result_ref=?,updated_at=? WHERE task_id=? AND official_request_id=? AND status='running' AND updated_at=? AND result_ref IS NULL")
     .run(resultRef,Math.max(Date.now(),claim.claimStamp+1),taskId,officialRequestId,claim.claimStamp).changes===1,'AUDIO_COMPLETE_CHANGED');
   });
   return {state:'completed',taskId,resultRef,...(targetDurationSeconds===undefined?{}:{
    targetDurationSeconds,actualDurationSeconds:verified.duration,targetReached:verified.duration>=targetDurationSeconds})};
  }catch(e){
   if(!publishing||e instanceof AudioDefinitelyRejected){
    try{tx(db,()=>{
     sameClaim(db,claim,found);
     check(markWorkflowTaskFailed(db,{...claim,workerType:WORKER}),'AUDIO_FAILURE_CLAIM_CHANGED');
    });const failure={state:'failed',taskId,code:publishing?'AUDIO_PUBLICATION_DEFINITELY_REJECTED':signal?.aborted?'AUDIO_CANCELLED':code};
     if(failure.code==='AUDIO_LYRIC_FAILED'){
      failure.providerPhase='lyrics';
      failure.providerCode=typeof e?.message==='string'&&/^AUDIO_LYRIC_[A-Z_]{2,40}$/u.test(e.message)?e.message:'AUDIO_LYRIC_UNCLASSIFIED';
     }
     if(failure.code==='AUDIO_CAPTION_FAILED'){
      failure.providerPhase='caption';
      failure.providerCode=typeof e?.message==='string'&&/^AUDIO_CAPTION_[A-Z_]{2,40}$/u.test(e.message)?e.message:'AUDIO_CAPTION_UNCLASSIFIED';
     }
     if(failure.code==='AUDIO_RENDER_FAILED'&&['setup','plan','render','verify','unknown'].includes(e?.providerPhase)&&
        typeof e.providerCode==='string'&&/^(?:AUDIO_HQ_|HQ_)[A-Z0-9_]{1,52}$/.test(e.providerCode)){
      failure.providerPhase=e.providerPhase;failure.providerCode=e.providerCode;
     }
     return failure;}catch{}
   }
   return {state:'requires_reconciliation',taskId,code:publishing?'AUDIO_PUBLICATION_UNCERTAIN':'AUDIO_CLAIM_CHANGED'};
  }
 }finally{db.close();}
}
