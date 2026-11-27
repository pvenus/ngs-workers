import {readFile,access} from 'node:fs/promises';
import {constants} from 'node:fs';
import {DatabaseSync} from 'node:sqlite';
import {createHash} from 'node:crypto';
import {mapCanonical as canonical,mapFields as exact,officialId,validMapGraph} from './map-contract.mjs';
import {assertDatabasePath,protectSchema,documentTaskEligibleSql,workflowTaskRetrySetSql,markWorkflowTaskFailed} from './workflow-ready.mjs';

import {executeTypedCodexDocument,REPRESENTATIVE_CODEX_PATH} from './representative-renderer.mjs';
import {assemblyTypes,assemblyStage,assemblyPds,assemblyRef,validateAssembly,validPuzzles,assemblyPayloadSchemas} from './assembly-contract.mjs';
export {assemblyTypes,assemblyStage,assemblyPds};
export const assemblyCapability='workflow.assembly.v1';
const sha=x=>createHash('sha256').update(x).digest('hex');
const rev=x=>Number.isSafeInteger(x)&&x>=1;
const immutable=`task_id=? AND official_request_id=? AND worker_type='document' AND task_key=? AND task_type=?
 AND depends_on_task_id IS NULL AND result_ref IS NULL`;
const generation=assemblyTypes[0];
const {id,layout,attachments}=assemblyPayloadSchemas[1].properties.puzzles.items.properties;
const outputSchema={type:'object',properties:{layout,attachments},required:['layout','attachments'],additionalProperties:false};
const batchOutputSchema={type:'object',properties:{puzzles:{type:'array',minItems:1,maxItems:8,items:{
 type:'object',properties:{id,layout,attachments},required:['id','layout','attachments'],additionalProperties:false
}}},required:['puzzles'],additionalProperties:false};
export function createAssemblyRenderer({execute=executeTypedCodexDocument,codexPath=REPRESENTATIVE_CODEX_PATH}={}){
 return async (input,{signal}={})=>{
  await access(codexPath,constants.X_OK);
  const guide=await readFile(new URL('./guides/assembly-placement.md',import.meta.url),'utf8');
  const batch=Array.isArray(input.puzzles);
  const task=batch?'\n\nFor this batch, return exactly one entry per input puzzle ID in a puzzles array. Consider the overall intent together with each puzzle’s own intent; plan all placements as one assembly. Do not omit, duplicate or invent a puzzle ID.':'';
  return execute({codexPath,signal,prompt:guide+task+'\n\n'+JSON.stringify(input),outputSchema:batch?batchOutputSchema:outputSchema});
 };
}
function validContext(context,title,puzzle){
 return exact(context,['regions','connections','startRegionId'])&&validMapGraph({...context,title})&&
  ['altar','well'].every(role=>context.regions.some(r=>r.id===puzzle.layout[role].regionId));
}
export async function runAssemblyFromPath({dbPath,taskId,officialRequestId,operation,pds,render,signal}){
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
   if(row.schema_version!==p?.schemaVersion||row.task_input!==row.payload||
      validateAssembly(p,operation)!==row.payload||sha(row.payload)!==row.payload_sha256)throw Error('ASSEMBLY_TASK_BINDING_INVALID');
   const claimStamp=Math.max(Date.now(),row.updated_at+1,row.registered_at);
   if(!Number.isSafeInteger(claimStamp))throw Error('ASSEMBLY_TASK_BINDING_INVALID');
   const n=db.prepare(`UPDATE workflow_tasks SET status='running',updated_at=?,${workflowTaskRetrySetSql}
    WHERE ${immutable} AND updated_at=?`).run(claimStamp,...scope,row.updated_at);
   if(n.changes!==1)throw Error('ASSEMBLY_CLAIM_CHANGED');
   claim={taskId,officialRequestId,projectId:'binari',claimStamp,payloadSha256:row.payload_sha256};
   db.exec('COMMIT');
  }catch(e){db.exec('ROLLBACK');throw e;}
  let stage='prepare';
  try{
   const assemblyId=p.assemblyId??officialRequestId,revision=(p.expectedRevision??0)+1;
   const resultRef=assemblyRef(assemblyId,revision);
   const generating=operation===generation;
   const batch=generating&&Array.isArray(p.puzzles);
   const prepared=await pds.readAssembly(claim,signal),context=prepared?.context;
   let state=prepared?.state;
   if(!exact(prepared,generating?['state','context']:['state'])||!exact(state,['schemaVersion','projectId','assemblyId','revision','updatedAt','officialRequestId','title','map','puzzles'])||
      state.schemaVersion!=='ngs.assembly.v1'||state.projectId!=='binari'||state.assemblyId!==assemblyId||state.revision!==revision||
      state.officialRequestId!==officialRequestId||(!rev(state.updatedAt)||state.updatedAt>claim.claimStamp)||!validPuzzles(state.puzzles)||
      !exact(state.map,['mapId','revision','officialRequestId'])||!officialId(state.map.mapId)||!rev(state.map.revision)||
      !officialId(state.map.officialRequestId)||(p.mapId!==undefined&&(state.map.mapId!==p.mapId||state.title!==p.title))||
      (!generating&&canonical(state.puzzles)!==canonical(p.puzzles))||
      (generating&&(state.puzzles.length!==(batch?p.puzzles.length:1)||!state.puzzles.every((row,i)=>
       validContext(context,state.title,row)&&(!batch||(row.id===p.puzzles[i].id&&row.type===p.puzzles[i].type))))))throw Error('ASSEMBLY_PREPARE_INVALID');
   if(generating&&state.updatedAt===claim.claimStamp){
    stage='cli';
    signal?.throwIfAborted();
    let placement;
    const input={title:p.title,intent:p.intent,map:context,...(batch?
     {puzzles:p.puzzles.map((row,i)=>({...row,bindings:state.puzzles[i].bindings}))}:{bindings:state.puzzles[0].bindings})};
    try{placement=await render(input,{signal});}
    catch{throw Error('ASSEMBLY_CLI_FAILED');}
    let placements;
    if(batch){
     if(!exact(placement,['puzzles'])||!Array.isArray(placement.puzzles)||placement.puzzles.length!==state.puzzles.length||
        !placement.puzzles.every(row=>exact(row,['id','layout','attachments'])))throw Error('ASSEMBLY_CLI_OUTPUT_INVALID');
     placements=new Map(placement.puzzles.map(row=>[row.id,row]));
     if(placements.size!==state.puzzles.length||!state.puzzles.every(row=>placements.has(row.id)))throw Error('ASSEMBLY_CLI_OUTPUT_INVALID');
    }else if(!exact(placement,['layout','attachments']))throw Error('ASSEMBLY_CLI_OUTPUT_INVALID');
    const puzzles=state.puzzles.map(row=>{
     const {layout,attachments}=batch?placements.get(row.id):placement;
     return {...row,layout,attachments};
    });
    if(!validPuzzles(puzzles)||!puzzles.every(row=>validContext(context,state.title,row)))throw Error('ASSEMBLY_CLI_OUTPUT_INVALID');
    state={...state,puzzles};
   }
   signal?.throwIfAborted();
   stage='publish';
   const receipt=await pds.publishAssembly(claim,state,signal);
   if(!exact(receipt,['ok','resultRef'])||receipt.ok!==true||receipt.resultRef!==resultRef)throw Error('ASSEMBLY_SAVE_NOT_CONFIRMED');
   stage='complete';
   const n=db.prepare(`UPDATE workflow_tasks SET status='completed',result_ref=?,updated_at=?
    WHERE ${immutable} AND status='running' AND updated_at=? AND EXISTS (SELECT 1 FROM workflow_runs r WHERE
    r.official_request_id=workflow_tasks.official_request_id AND r.state='planned' AND r.reason IS NULL AND r.payload_sha256=?)`)
    .run(resultRef,Math.max(Date.now(),claim.claimStamp+1),...scope,claim.claimStamp,claim.payloadSha256);
   return n.changes===1?{state:'completed',taskId,resultRef}:{state:'not_claimable',taskId};
  }catch(e){
   const failed=markWorkflowTaskFailed(db,{...claim,workerType:'document'});
   const allowed=['ASSEMBLY_PREPARE_INVALID','ASSEMBLY_CLI_FAILED','ASSEMBLY_CLI_OUTPUT_INVALID','ASSEMBLY_SAVE_NOT_CONFIRMED',
    'ASSEMBLY_REVISION_CONFLICT','ASSEMBLY_PROP_UNAVAILABLE','ASSEMBLY_ROUTE_NOT_PLAYABLE','ASSEMBLY_INPUT_CHANGED',
    'WORKFLOW_CINEMATIC_BAD_BODY','WORKFLOW_CINEMATIC_DENIED','WORKFLOW_CINEMATIC_INTERNAL_ONLY',
    'WORKFLOW_CINEMATIC_NOT_FOUND','WORKFLOW_CINEMATIC_DISABLED','WORKFLOW_CINEMATIC_ROUTE',
    'WORKFLOW_CINEMATIC_NOT_READY','WORKFLOW_CINEMATIC_OUTCOME_UNCERTAIN','DOCUMENT_PDS_CONNECT_REFUSED_BEFORE_SEND'];
   const code=allowed.includes(e?.message)?e.message:`ASSEMBLY_${stage.toUpperCase()}_FAILED`;
   return {state:failed?'failed':'not_claimable',taskId,code,stage,
    ...(Number.isInteger(e?.pdsHttpStatus)&&e.pdsHttpStatus>=400&&e.pdsHttpStatus<=599?{pdsHttpStatus:e.pdsHttpStatus}:{}),
    ...(['deadline_exceeded','caller_aborted','http_error','connection_refused','connection_reset','response_aborted',
       'response_stream_error','response_too_large','invalid_content_type','invalid_json','unexpected_assembly_error_response',
       'connection_error','request_error'].includes(e?.pdsTransportKind)?{pdsTransportKind:e.pdsTransportKind}:{})};
  }
 }finally{db.close();}
}
