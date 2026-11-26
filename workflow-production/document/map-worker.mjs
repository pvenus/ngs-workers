import {DatabaseSync} from 'node:sqlite';
import {createHash} from 'node:crypto';
import {readFile,access} from 'node:fs/promises';
import {constants} from 'node:fs';
import {assertDatabasePath,protectSchema,documentTaskEligibleSql,workflowTaskRetrySetSql,markWorkflowTaskFailed} from './workflow-ready.mjs';
import {mapTypes,mapStage,mapRoot,mapCanonical,mapFields,validMapRequest,applyMapCoordinates,backgroundId,mapBackgroundIds} from './map-contract.mjs';
import {executeTypedCodexDocument,REPRESENTATIVE_CODEX_PATH} from './representative-renderer.mjs';
const sha=x=>createHash('sha256').update(x).digest('hex');
const integer={type:'integer',minimum:0,maximum:1000};
const object=properties=>({type:'object',properties,required:Object.keys(properties),additionalProperties:false});
const str={type:'string'};
const output=object({coordinates:{type:'array',items:object({id:str,hit:object({x:integer,y:integer,width:integer,height:integer}),spawn:object({x:integer,y:integer})})}});
export function createMapRenderer({execute=executeTypedCodexDocument,codexPath=REPRESENTATIVE_CODEX_PATH}={}){
 return async (input,{signal,imageBytes}={})=>{
  await access(codexPath,constants.X_OK);
  const guide=await readFile(new URL('./guides/map-coordinates.md',import.meta.url),'utf8');
  if(imageBytes?.length!==input.attachmentCount||imageBytes.length<1)throw Error('MAP_IMAGE_MISSING');
  return execute({codexPath,signal,prompt:guide+'\n\n'+JSON.stringify(input),outputSchema:output,imageBytes});
 };
}
const immutable=`task_id=? AND official_request_id=? AND worker_type='document' AND task_key='map_metadata' AND task_type=?
 AND depends_on_task_id IS NULL AND result_ref IS NULL`;
export async function runMapFromPath({dbPath,taskId,officialRequestId,operation,pds,render,signal}){
 if(!mapTypes.includes(operation)||taskId!=='workflow.task.'+sha(mapCanonical([officialRequestId,mapStage])))throw Error('MAP_TASK_INVALID');
 assertDatabasePath(dbPath);
 if(signal?.aborted)return {state:'stopping'};
 const db=new DatabaseSync(dbPath);
 try{
  db.exec('PRAGMA busy_timeout=5000; PRAGMA foreign_keys=ON');protectSchema(db);
  const scope=[taskId,officialRequestId,operation];
  let claim,p;
  db.exec('BEGIN IMMEDIATE');
  try{
   const row=db.prepare(`SELECT t.task_input,r.payload,r.payload_sha256,t.updated_at,r.registered_at,r.schema_version
    FROM workflow_tasks t JOIN workflow_runs r USING(official_request_id)
    WHERE ${immutable.replaceAll('official_request_id=?','t.official_request_id=?')}
    AND r.project_id='binari' AND r.request_type=t.task_type AND r.state='planned' AND r.reason IS NULL AND ${documentTaskEligibleSql(db)}`).get(...scope);
   if(!row){db.exec('COMMIT');return {state:'not_claimable',taskId};}
   p=JSON.parse(row.payload);
   if(!validMapRequest(p,operation)||p.schemaVersion!==row.schema_version||mapCanonical(p)!==row.payload||row.task_input!==row.payload||sha(row.payload)!==row.payload_sha256)
    throw Error('MAP_TASK_BINDING_INVALID');
   const claimStamp=Math.max(Date.now(),row.updated_at+1,row.registered_at);
   if(!Number.isSafeInteger(claimStamp))throw Error('MAP_TASK_BINDING_INVALID');
   const n=db.prepare(`UPDATE workflow_tasks SET status='running',updated_at=?,${workflowTaskRetrySetSql} WHERE ${immutable} AND updated_at=?`).run(claimStamp,...scope,row.updated_at);
   if(n.changes!==1)throw Error('MAP_CLAIM_CHANGED');
   claim={taskId,officialRequestId,projectId:'binari',claimStamp,payloadSha256:row.payload_sha256};
   db.exec('COMMIT');
  }catch(e){db.exec('ROLLBACK');throw e;}
  try{
   let graph=p.graph;
   if(operation===mapTypes[0]){
    const context=await pds.readMap(claim,signal), ids=mapBackgroundIds(graph);
    if(!mapFields(context,['backgrounds'])||!Array.isArray(context.backgrounds)||context.backgrounds.length!==ids.length||
       !context.backgrounds.every((r,i)=>mapFields(r,['id','title','sha256'])&&backgroundId(r.id)&&r.id===ids[i]&&typeof r.title==='string'))
      throw Error('MAP_BACKGROUNDS_UNAVAILABLE');
    const imageBytes=[],images=[],seen=new Map();
    for(const r of graph.regions){
     if(context.backgrounds.find(x=>x.id===r.backgroundId)?.sha256!==r.imageSha256)throw Error('MAP_IMAGE_CHANGED');
     if(seen.has(r.imageSha256)){images.push({regionId:r.id,attachment:seen.get(r.imageSha256)});continue;}
     const item=await pds.readMapImage(claim,r.imageSha256,signal);
     if(!mapFields(item,['sourceSha256','title','base64'])||item.sourceSha256!==r.imageSha256||typeof item.base64!=='string'||item.base64.length>11200000)throw Error('MAP_IMAGE_INVALID');
     const bytes=Buffer.from(item.base64,'base64');
     if(bytes.length>8*1024*1024||!bytes.subarray(0,8).equals(Buffer.from('89504e470d0a1a0a','hex'))||sha(bytes)!==r.imageSha256)throw Error('MAP_IMAGE_INVALID');
     imageBytes.push(bytes);seen.set(r.imageSha256,imageBytes.length);images.push({regionId:r.id,attachment:imageBytes.length});
    }
    graph=applyMapCoordinates(graph,await render({intent:p.intent,startRegionId:graph.startRegionId,regions:graph.regions,connections:graph.connections,
     images,attachmentCount:imageBytes.length},{signal,imageBytes}));
   }
   signal?.throwIfAborted();
   const resultRef='binari/'+mapRoot+'/'+(p.mapId??officialRequestId)+'/'+((p.expectedRevision??0)+1)+'.json';
   const receipt=await pds.publishMap(claim,graph);
   if(!mapFields(receipt,['ok','resultRef'])||receipt.ok!==true||receipt.resultRef!==resultRef)throw Error('MAP_SAVE_NOT_CONFIRMED');
   const n=db.prepare(`UPDATE workflow_tasks SET status='completed',result_ref=?,updated_at=?
    WHERE ${immutable} AND status='running' AND updated_at=? AND EXISTS (SELECT 1 FROM workflow_runs r WHERE
    r.official_request_id=workflow_tasks.official_request_id AND r.state='planned' AND r.reason IS NULL AND r.payload_sha256=?)`)
    .run(resultRef,Math.max(Date.now(),claim.claimStamp+1),...scope,claim.claimStamp,claim.payloadSha256);
   if(n.changes!==1)return {state:'not_claimable',taskId};
   return {state:'completed',taskId,resultRef};
  }catch(e){
   const failed=markWorkflowTaskFailed(db,{...claim,workerType:'document'});
   return {state:failed?'failed':'not_claimable',taskId,code:/^MAP_[A-Z_]+$/.test(e?.message)?e.message:'MAP_WORKFLOW_FAILED'};
  }
 }finally{db.close();}
}
