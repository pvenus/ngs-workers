import {retrySqliteBusy,saveReservationClearSql} from './sqlite-busy-retry.mjs';
/** This resident lane can publish only a complete supported prop plan. */
import {DatabaseSync} from 'node:sqlite';
import {canonical,sha} from './vendor/planner-f24d23a/envelope.mjs';
import {assertDatabasePath,protectSchema,workflowTaskEligibleSql,workflowTaskRetrySetSql,markWorkflowTaskFailed} from './workflow-ready.mjs';
import {exactProp,propValidator,propGenerateType,propRegenerateType,propAnimationType,propScaleType} from './vendor/prop-composite/prop-generate-envelope.mjs';
import {storedPropPlan,checkPropPublishRows,exactPropTask,isPropRef,isPropScaleKey,verifyPropScaleRefs,propCompletionLimit,propKeys} from './vendor/prop-composite/prop-generate-plan.mjs';
import {parsePropPackage,propPackageReceipt} from './vendor/prop-composite/prop-package-contract.mjs';

const types=[propGenerateType,propRegenerateType];
const stillSql="((r.request_type IN (?,?) AND CASE WHEN json_valid(r.payload) THEN coalesce(json_extract(r.payload,'$.motion'),'still')='still' ELSE 0 END) OR r.request_type='prop.scale.generate')";
const modeSql=appendEnabled=>appendEnabled
 ?`((${stillSql}) OR r.request_type='${propAnimationType}')`
 :stillSql;
const check=(ok,code='WORKFLOW_PROP_PACKAGE_BINDING_INVALID')=>{if(!ok)throw Error(code);};

function tx(db,fn){
 db.exec('BEGIN IMMEDIATE');try{const v=fn();db.exec('COMMIT');return v;}catch(e){db.exec('ROLLBACK');throw e;}
}
function get(db,officialRequestId,taskId,{stamp,appendEnabled=false}={}){
 const root=db.prepare(`SELECT r.* FROM workflow_runs r WHERE r.official_request_id=?
   AND r.state='planned' AND r.reason IS NULL AND r.project_id='binari' AND ${modeSql(appendEnabled)}`).get(officialRequestId,...types);
 const predicate=stamp===undefined?workflowTaskEligibleSql(db, 't'):"t.status='running' AND t.updated_at=?";
 const pick=db.prepare(`SELECT t.* FROM workflow_tasks t WHERE t.official_request_id=? AND t.task_id=?
  AND t.result_ref IS NULL AND ${predicate}`).get(officialRequestId,taskId,...(stamp===undefined?[]:[stamp]));
 if(!root||!pick)return null;
 const payload=JSON.parse(root.payload);
 const run={officialRequestId,requestType:root.request_type,payload};
 const rows=db.prepare('SELECT * FROM workflow_tasks WHERE official_request_id=? ORDER BY task_key LIMIT '+propCompletionLimit).all(officialRequestId);
 const tasks=storedPropPlan(run,rows),chosen=tasks.find(t=>t.id===taskId);
 check(root.schema_version===payload.schemaVersion&&propValidator[root.request_type](payload,root.project_id)===root.payload&&
   sha(root.payload)===root.payload_sha256&&chosen&&exactPropTask(pick,chosen));
 if(isPropScaleKey(chosen.key)){
  const byId=new Map(rows.map(row=>[row.task_id,row]));
  check(rows.length===tasks.length&&tasks.every(task=>exactPropTask(byId.get(task.id),task)));
  const parent=chosen.dependsOn===null?null:byId.get(chosen.dependsOn);
  if(parent&&!(parent.status==='completed'&&isPropRef(parent.task_key,parent.result_ref)))return null;
  return {root,pick,payload,refs:parent?[parent.result_ref]:[],scaleKey:chosen.key};
 }
 check(chosen.key===propKeys.publish);
 const refs=checkPropPublishRows(run,rows.map(row=>row.task_id===taskId&&stamp===undefined?
  {...row,status:'running',updated_at:Math.max(1,row.updated_at)}:row));
 if(!refs)return null;
 return {root,pick,payload,refs};
}

export function nextPropPackageFromPath(dbPath,{appendEnabled=false}={}){
 assertDatabasePath(dbPath);
 const db=new DatabaseSync(dbPath,{readOnly:true});
 try{
  protectSchema(db);
  const parentKeys=Object.values(propKeys).filter(k=>[propKeys.design,propKeys.base,propKeys.detail,propKeys.pixel].includes(k));
  const row=db.prepare(`SELECT t.task_id AS taskId,t.official_request_id AS officialRequestId
    FROM workflow_tasks t JOIN workflow_runs r USING(official_request_id)
    WHERE r.state='planned' AND r.reason IS NULL AND r.project_id='binari' AND ${modeSql(appendEnabled)}
      AND ${workflowTaskEligibleSql(db, 't')} AND t.result_ref IS NULL AND t.worker_type='postprocess'
      AND (t.task_type='prop.scale.derive' OR (t.task_key=?
      AND CASE WHEN r.request_type='${propScaleType}' THEN 1 WHEN r.request_type='${propAnimationType}' THEN
        (SELECT count(*) FROM workflow_tasks w WHERE w.official_request_id=t.official_request_id
         AND w.task_key IN (?,?,?) AND w.status='completed' AND w.result_ref IS NOT NULL)=3 ELSE
        (SELECT count(*) FROM workflow_tasks w WHERE w.official_request_id=t.official_request_id
         AND w.task_key IN (?,?,?,?) AND w.status='completed' AND w.result_ref IS NOT NULL)=4 AND (json_extract(r.payload,'$.presentationProfile') IS NULL OR EXISTS (
         SELECT 1 FROM workflow_tasks w WHERE w.official_request_id=t.official_request_id AND w.task_key=?
          AND w.status='completed' AND w.result_ref IS NOT NULL)) END))
    ORDER BY r.registered_at,r.official_request_id LIMIT 1`).get(...types,propKeys.publish,propKeys.motion,propKeys.animDetail,propKeys.animPixel,...parentKeys,propKeys.spirit);
  return row?{taskId:row.taskId,officialRequestId:row.officialRequestId}:null;
 }finally{db.close();}
}
export async function runPropPackageFromPath({dbPath,officialRequestId,taskId,pds,signal,appendEnabled=false}){
 assertDatabasePath(dbPath);
 check(/^request\.official\.[a-f0-9]{64}$/u.test(officialRequestId)&&/^workflow\.task\.[a-f0-9]{64}$/u.test(taskId));
 const db=new DatabaseSync(dbPath);
 try{
  db.exec('PRAGMA foreign_keys=ON; PRAGMA busy_timeout=5000;');protectSchema(db);
  if(signal?.aborted)return {state:'not_claimable',taskId};
  const attempt=tx(db,()=>{
   const found=get(db,officialRequestId,taskId,{appendEnabled});
   if(!found)return null;
   const stamp=Math.max(Date.now(),found.pick.updated_at+1);
   check(Number.isSafeInteger(stamp)&&stamp<Number.MAX_SAFE_INTEGER);
   check(db.prepare(`UPDATE workflow_tasks SET status='running',updated_at=?,${workflowTaskRetrySetSql}
     WHERE task_id=? AND official_request_id=? AND updated_at=? AND result_ref IS NULL AND ${workflowTaskEligibleSql(db)}`)
    .run(stamp,taskId,officialRequestId,found.pick.updated_at).changes===1);
   return {found,claim:{officialRequestId,taskId,projectId:found.root.project_id,payloadSha256:found.root.payload_sha256,claimStamp:stamp}};
  });
  if(!attempt)return {state:'not_claimable',taskId};
  const {found,claim}=attempt,{claimStamp:stamp}=claim;
  const current=()=>{
   const confirmed=get(db,officialRequestId,taskId,{stamp,appendEnabled});
   check(confirmed&&canonical(confirmed.root)===canonical(found.root)&&canonical(confirmed.refs)===canonical(found.refs),
    'WORKFLOW_PROP_PACKAGE_CLAIM_CHANGED');
  };
  const complete=expected=>retrySqliteBusy(()=>{
   tx(db,()=>{
    current();
    check(db.prepare(`UPDATE workflow_tasks SET status='completed',result_ref=?,updated_at=?${saveReservationClearSql(db)}
     WHERE task_id=? AND official_request_id=? AND status='running' AND updated_at=? AND result_ref IS NULL`)
     .run(expected,Math.max(Date.now(),stamp+1),taskId,officialRequestId,stamp).changes===1);
   });
  },taskId);
  let sent=false;
  try{
   if(found.scaleKey){
    signal?.throwIfAborted();tx(db,current);
    // Only private immutable staging is written. Retrying an exact pair is safe.
    const ack=await pds.derivePropScale(claim);
    check(exactProp(ack,['ok','resultRef'])&&ack.ok===true&&isPropRef(found.scaleKey,ack.resultRef),
     'WORKFLOW_PROP_SCALE_STAGE_ACK');
    await complete(ack.resultRef);
    return {state:'completed',taskId,resultRef:ack.resultRef};
   }
   const prepared=await pds.preparePropPublication(claim,{signal});
   check(exactProp(prepared,['manifest']));
   const m=parsePropPackage(prepared.manifest);
   check(verifyPropScaleRefs(m,found.refs));
   const source=found.payload.source;
   const shaFor=k=>found.refs.find(x=>x.key===k)?.ref.slice(-64);
   check(m.officialRequestId===officialRequestId&&m.requestSha256===claim.payloadSha256&&
    m.propId===found.payload.propId&&m.revision===(source?source.revision+1:1)&&m.parentSha256===(source?.sha256??null));
   if(found.root.request_type===propAnimationType){
    const clip=m.animations.at(-1);
    check(clip?.id===found.payload.motionId&&clip.officialRequestId===officialRequestId&&clip.requestSha256===claim.payloadSha256&&
     clip.media.detail.sha256===shaFor(propKeys.animDetail)&&clip.media.pixel.sha256===shaFor(propKeys.animPixel));
   }else if(found.root.request_type===propScaleType){
    check(found.payload.scalePercents.every(n=>m.scaleVariants?.some(v=>v.scalePercent===n)));
   }else{
    check(m.designContextSha256===found.payload.designContextSha256&&m.animations.length===0&&
     m.media.detail.sha256===shaFor(propKeys.detail)&&m.media.pixel.sha256===shaFor(propKeys.pixel)&&
     m.media.spirit?.sha256===shaFor(propKeys.spirit));
    if(found.payload.scalePercents)check(canonical(m.scaleVariants.map(v=>v.scalePercent))===canonical([...found.payload.scalePercents].sort((a,b)=>a-b))&&
      m.scaleSourcePixel?.sha256===shaFor(propKeys.pixelDetail));
   }
   const expected=await propPackageReceipt(m);
   signal?.throwIfAborted();tx(db,current);
   sent=true;
   const resp=await pds.publishPropPackage(claim,m);
   check(exactProp(resp,['ok','resultRef'])&&resp.ok===true&&resp.resultRef===expected,'WORKFLOW_PROP_PACKAGE_ACK');
   await complete(expected);
   return {state:'completed',taskId,resultRef:expected};
  }catch{
   if(!sent)try{
    tx(db,()=>{current();check(markWorkflowTaskFailed(db,{...claim,workerType:'postprocess'}));});
    return {state:'failed',taskId,code:found.scaleKey?'WORKFLOW_PROP_SCALE_DERIVE_FAILED':'WORKFLOW_PROP_PACKAGE_PREPARE_FAILED'};
   }catch{}
   return {state:'requires_reconciliation',taskId,code:'WORKFLOW_PROP_PACKAGE_PUBLICATION_UNCERTAIN'};
  }
 }finally{db.close();}
}
