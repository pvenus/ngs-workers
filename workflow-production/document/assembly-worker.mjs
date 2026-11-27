import {DatabaseSync} from 'node:sqlite';
import {createHash} from 'node:crypto';
import {mapCanonical as canonical,mapFields as exact,officialId,mapText} from './map-contract.mjs';
import {assertDatabasePath,protectSchema,documentTaskEligibleSql,workflowTaskRetrySetSql,markWorkflowTaskFailed} from './workflow-ready.mjs';

export const assemblyTypes=['assembly.generate','assembly.update'];
export const assemblyStage='assembly_metadata';
export const assemblyPds='/v1/assemblies/ownership/workflow-assembly';
export const assemblyCapability='workflow.assembly.v1';
const sha=x=>createHash('sha256').update(x).digest('hex');
const rev=x=>Number.isSafeInteger(x)&&x>=1;
const immutable=`task_id=? AND official_request_id=? AND worker_type='document' AND task_key=? AND task_type=?
 AND depends_on_task_id IS NULL AND result_ref IS NULL`;
function validPayload(p,operation){
 const generating=operation===assemblyTypes[0];
 return exact(p,generating?['schemaVersion','projectId','title','mapId','puzzleType','intent']:
  ['schemaVersion','projectId','assemblyId','expectedRevision','puzzles'])&&p.projectId==='binari'&&
  p.schemaVersion===`ngs.assembly-${generating?'generate':'update'}.v1`&&
  (generating?officialId(p.mapId)&&mapText(p.title,100)&&p.puzzleType==='binari.ritual-well-water-demo'&&mapText(p.intent,2000):
   officialId(p.assemblyId)&&rev(p.expectedRevision)&&p.expectedRevision<Number.MAX_SAFE_INTEGER&&Array.isArray(p.puzzles));
}
export async function runAssemblyFromPath({dbPath,taskId,officialRequestId,operation,pds,signal}){
 if(!assemblyTypes.includes(operation)||!officialId(officialRequestId)||
    taskId!=='workflow.task.'+sha(canonical([officialRequestId,assemblyStage])))throw Error('ASSEMBLY_TASK_INVALID');
 assertDatabasePath(dbPath);
 if(signal?.aborted)return {state:'stopping'};
 const db=new DatabaseSync(dbPath);
 try{
  db.exec('PRAGMA busy_timeout=5000; PRAGMA foreign_keys=ON');protectSchema(db);
  const scope=[taskId,officialRequestId,assemblyStage,operation];
  let claim,p;
  db.exec('BEGIN IMMEDIATE');
  try{
   const columns=new Set(db.prepare('PRAGMA table_info(workflow_tasks)').all().map(c=>c.name));
   const additional=[2,3,4,5].filter(n=>columns.has('depends_on_task_id_'+n))
     .map(n=>' AND t.depends_on_task_id_'+n+' IS NULL').join('');
   const row=db.prepare(`SELECT t.task_input,r.payload,r.payload_sha256,t.updated_at,r.registered_at,r.schema_version
    FROM workflow_tasks t JOIN workflow_runs r USING(official_request_id)
    WHERE ${immutable.replace('official_request_id=?','t.official_request_id=?')}${additional}
    AND r.project_id='binari' AND r.request_type=t.task_type AND r.state='planned' AND r.reason IS NULL AND ${documentTaskEligibleSql(db)}`).get(...scope);
   if(!row){db.exec('COMMIT');return {state:'not_claimable',taskId};}
   p=JSON.parse(row.payload);
   if(!validPayload(p,operation)||row.schema_version!==p.schemaVersion||row.task_input!==row.payload||
      canonical(p)!==row.payload||sha(row.payload)!==row.payload_sha256)throw Error('ASSEMBLY_TASK_BINDING_INVALID');
   const claimStamp=Math.max(Date.now(),row.updated_at+1,row.registered_at);
   if(!Number.isSafeInteger(claimStamp))throw Error('ASSEMBLY_TASK_BINDING_INVALID');
   const n=db.prepare(`UPDATE workflow_tasks SET status='running',updated_at=?,${workflowTaskRetrySetSql}
    WHERE ${immutable} AND updated_at=?`).run(claimStamp,...scope,row.updated_at);
   if(n.changes!==1)throw Error('ASSEMBLY_CLAIM_CHANGED');
   claim={taskId,officialRequestId,projectId:'binari',claimStamp,payloadSha256:row.payload_sha256};
   db.exec('COMMIT');
  }catch(e){db.exec('ROLLBACK');throw e;}
  try{
   const assemblyId=p.assemblyId??officialRequestId,revision=(p.expectedRevision??0)+1;
   const resultRef=`binari/cinematics/assemblies/${assemblyId}/${revision}.json`;
   const context=await pds.readAssembly(claim,signal),state=context?.state;
   if(!exact(context,['state'])||!exact(state,['schemaVersion','projectId','assemblyId','revision','updatedAt','officialRequestId','title','map','puzzles'])||
      state.schemaVersion!=='ngs.assembly.v1'||state.projectId!=='binari'||state.assemblyId!==assemblyId||state.revision!==revision||
      state.officialRequestId!==officialRequestId||state.updatedAt!==claim.claimStamp||!Array.isArray(state.puzzles)||
      !exact(state.map,['mapId','revision','officialRequestId'])||!officialId(state.map.mapId)||!rev(state.map.revision)||
      !officialId(state.map.officialRequestId)||(p.mapId!==undefined&&(state.map.mapId!==p.mapId||state.title!==p.title))||
      (p.puzzles!==undefined&&canonical(state.puzzles)!==canonical(p.puzzles)))throw Error('ASSEMBLY_PREPARE_INVALID');
   signal?.throwIfAborted();
   const receipt=await pds.publishAssembly(claim,state,signal);
   if(!exact(receipt,['ok','resultRef'])||receipt.ok!==true||receipt.resultRef!==resultRef)throw Error('ASSEMBLY_SAVE_NOT_CONFIRMED');
   const n=db.prepare(`UPDATE workflow_tasks SET status='completed',result_ref=?,updated_at=?
    WHERE ${immutable} AND status='running' AND updated_at=? AND EXISTS (SELECT 1 FROM workflow_runs r WHERE
    r.official_request_id=workflow_tasks.official_request_id AND r.state='planned' AND r.reason IS NULL AND r.payload_sha256=?)`)
    .run(resultRef,Math.max(Date.now(),claim.claimStamp+1),...scope,claim.claimStamp,claim.payloadSha256);
   return n.changes===1?{state:'completed',taskId,resultRef}:{state:'not_claimable',taskId};
  }catch(e){
   const failed=markWorkflowTaskFailed(db,{...claim,workerType:'document'});
   return {state:failed?'failed':'not_claimable',taskId,code:/^ASSEMBLY_[A-Z_]+$/.test(e?.message)?e.message:'ASSEMBLY_WORKFLOW_FAILED'};
  }
 }finally{db.close();}
}
