import {DatabaseSync} from 'node:sqlite';
import {createHash} from 'node:crypto';
import {readFile,access} from 'node:fs/promises';
import {constants} from 'node:fs';
import {assertDatabasePath,protectSchema,documentTaskEligibleSql,workflowTaskRetrySetSql,markWorkflowTaskFailed} from './workflow-ready.mjs';
import {mapTypes,mapStage,mapRoot,mapGenerateDesignSchema,mapDocumentStages,mapCanonical,mapFields,validMapRequest,validMapGraph,applyMapCoordinates,mapPoint,backgroundId,mapBackgroundIds} from './map-contract.mjs';
import {executeTypedCodexDocument,REPRESENTATIVE_CODEX_PATH} from './representative-renderer.mjs';
const sha=x=>createHash('sha256').update(x).digest('hex');
const integer={type:'integer',minimum:0,maximum:1000};
const object=properties=>({type:'object',properties,required:Object.keys(properties),additionalProperties:false});
const str={type:'string'};
const rectOutput=object({x:integer,y:integer,width:{...integer,minimum:1},height:{...integer,minimum:1}});
const stageRenderer={
 start:{guide:'map-start.md',schema:object({startRegionId:str,startPoint:object({x:integer,y:integer})})},
 portals:{guide:'map-coordinates.md',schema:object({coordinates:{type:'array',items:object({id:str,hit:rectOutput,spawn:object({x:integer,y:integer})})}})},
 collisions:{guide:'map-collisions.md',schema:object({colliders:{type:'array',maxItems:96,items:object({
  id:str,regionId:str,rect:rectOutput,shape:{type:'string',enum:['rect','ellipse']},rotation:{type:'integer',minimum:0,maximum:359}
 })}})},
};
export function createMapRenderer({execute=executeTypedCodexDocument,codexPath=REPRESENTATIVE_CODEX_PATH}={}){
 return async (input,{signal,imageBytes}={})=>{
  await access(codexPath,constants.X_OK);
  const stage=Object.hasOwn(stageRenderer,input.stage)?stageRenderer[input.stage]:null;
  if(!stage)throw Error('MAP_CLI_STAGE_INVALID');
  const guide=await readFile(new URL('./guides/'+stage.guide,import.meta.url),'utf8');
  if(imageBytes?.length!==input.attachmentCount||imageBytes.length<1)throw Error('MAP_IMAGE_MISSING');
  return execute({codexPath,signal,prompt:guide+'\n\n'+JSON.stringify(input),outputSchema:stage.schema,imageBytes});
 };
}
async function runMapStage(render,input,signal,imageBytes){
 signal?.throwIfAborted();
 const begin=Date.now(),name=input.stage;let outcome='failed';
 try{
  const result=await render({...input,attachmentCount:imageBytes.length},{signal,imageBytes});
  outcome='returned';return result;
 }catch(error){
  if(signal?.aborted)throw error;
  throw Error('MAP_'+name.toUpperCase()+'_CLI_FAILED');
 }finally{
  console.log(JSON.stringify({event:'map_cli_stage',stage:name,outcome,durationMs:Math.max(0,Date.now()-begin)}));
 }
}
const immutable=`task_id=? AND official_request_id=? AND worker_type='document' AND task_key=? AND task_type=?
 AND coalesce(depends_on_task_id,'')=? AND result_ref IS NULL`;
const workflowId=(oid,key)=>'workflow.task.'+sha(mapCanonical([oid,key]));
const stageRef=value=>'workflow.map-stage.'+sha(mapCanonical(value));
function stageValue(db,oid,key){
 const id=workflowId(oid,key);
 const read=db.prepare(`SELECT map_part_json AS value_json,result_ref FROM workflow_tasks WHERE task_id=?
  AND official_request_id=? AND task_key=? AND task_type='map.generate' AND status='completed'`).get(id,oid,key);
 if(!read)throw Error('MAP_STAGE_MISSING');
 const value=JSON.parse(read.value_json);
 if(read.result_ref!==stageRef(value)||read.value_json!==mapCanonical(value))throw Error('MAP_STAGE_BINDING_INVALID');
 return value;
}
async function mapImages(claim,pds,whole,regions,signal){
 const context=await pds.readMap(claim,signal),ids=mapBackgroundIds(whole);
 if(!mapFields(context,['backgrounds'])||!Array.isArray(context.backgrounds)||context.backgrounds.length!==ids.length||
    !context.backgrounds.every((r,i)=>mapFields(r,['id','title','sha256'])&&backgroundId(r.id)&&r.id===ids[i]&&typeof r.title==='string'))
   throw Error('MAP_BACKGROUNDS_UNAVAILABLE');
 const imageBytes=[],images=[],seen=new Map();
 for(const r of regions){
  if(context.backgrounds.find(x=>x.id===r.backgroundId)?.sha256!==r.imageSha256)throw Error('MAP_IMAGE_CHANGED');
  if(seen.has(r.imageSha256)){images.push({regionId:r.id,attachment:seen.get(r.imageSha256)});continue;}
  const item=await pds.readMapImage(claim,r.imageSha256,signal);
  if(!mapFields(item,['sourceSha256','title','base64'])||item.sourceSha256!==r.imageSha256||typeof item.base64!=='string'||item.base64.length>11200000)throw Error('MAP_IMAGE_INVALID');
  const bytes=Buffer.from(item.base64,'base64');
  if(bytes.length>8*1024*1024||!bytes.subarray(0,8).equals(Buffer.from('89504e470d0a1a0a','hex'))||sha(bytes)!==r.imageSha256)throw Error('MAP_IMAGE_INVALID');
  imageBytes.push(bytes);seen.set(r.imageSha256,imageBytes.length);images.push({regionId:r.id,attachment:imageBytes.length});
 }
 return {imageBytes,images};
}
export async function runMapFromPath({dbPath,taskId,officialRequestId,operation,pds,render,signal}){
 const key=[mapStage,...mapDocumentStages].find(k=>taskId===workflowId(officialRequestId,k));
 if(!mapTypes.includes(operation)||!key||(key!==mapStage&&operation!==mapTypes[0]))throw Error('MAP_TASK_INVALID');
 const stageIndex=mapDocumentStages.indexOf(key),split=stageIndex!==-1;
 const predecessor=stageIndex>0?workflowId(officialRequestId,mapDocumentStages[stageIndex-1]):'';
 assertDatabasePath(dbPath);
 if(signal?.aborted)return {state:'stopping'};
 const db=new DatabaseSync(dbPath);
 try{
  db.exec('PRAGMA busy_timeout=5000; PRAGMA foreign_keys=ON');protectSchema(db);
  const scope=[taskId,officialRequestId,key,operation,predecessor];
  let claim,p;
  db.exec('BEGIN IMMEDIATE');
  try{
   const row=db.prepare(`SELECT t.task_input,r.payload,r.payload_sha256,t.updated_at,r.registered_at,r.schema_version
    FROM workflow_tasks t JOIN workflow_runs r USING(official_request_id)
    WHERE ${immutable.replaceAll('official_request_id=?','t.official_request_id=?')}
    AND r.project_id='binari' AND r.request_type=t.task_type AND r.state='planned' AND r.reason IS NULL AND ${documentTaskEligibleSql(db)}`).get(...scope);
   if(!row){db.exec('COMMIT');return {state:'not_claimable',taskId};}
   p=JSON.parse(row.payload);
   if(!validMapRequest(p,operation)||split!==(p.schemaVersion===mapGenerateDesignSchema)||p.schemaVersion!==row.schema_version||mapCanonical(p)!==row.payload||row.task_input!==row.payload||sha(row.payload)!==row.payload_sha256)
    throw Error('MAP_TASK_BINDING_INVALID');
   const claimStamp=Math.max(Date.now(),row.updated_at+1,row.registered_at);
   if(!Number.isSafeInteger(claimStamp))throw Error('MAP_TASK_BINDING_INVALID');
   const n=db.prepare(`UPDATE workflow_tasks SET status='running',updated_at=?,${workflowTaskRetrySetSql} WHERE ${immutable} AND updated_at=?`).run(claimStamp,...scope,row.updated_at);
   if(n.changes!==1)throw Error('MAP_CLAIM_CHANGED');
   claim={taskId,officialRequestId,projectId:'binari',claimStamp,payloadSha256:row.payload_sha256};
   db.exec('COMMIT');
  }catch(e){db.exec('ROLLBACK');throw e;}
  try{
   let graph=p.graph,resultRef,part=null;
   if(operation===mapTypes[0]){
    const chosen=graph.regions.find(r=>r.id===graph.startRegionId);
    const {imageBytes,images}=await mapImages(claim,pds,graph,stageIndex===0?[chosen]:graph.regions,signal);
    if(stageIndex===0){
     part=await runMapStage(render,{stage:'start',startRegionId:chosen.id,region:chosen,attachment:1,
      notes:p.design.startNotes},signal,imageBytes);
     if(!mapFields(part,['startRegionId','startPoint'])||part.startRegionId!==chosen.id||!mapPoint(part.startPoint))throw Error('MAP_START_CONFIRMATION_INVALID');
    }else{
     const start=split?stageValue(db,officialRequestId,mapDocumentStages[0]):{startRegionId:chosen.id};
     if(start?.startRegionId!==chosen.id||(split&&(!mapFields(start,['startRegionId','startPoint'])||!mapPoint(start.startPoint))))
      throw Error('MAP_START_CONFIRMATION_INVALID');
     if(stageIndex===2){
      graph={...applyMapCoordinates(graph,stageValue(db,officialRequestId,mapDocumentStages[1])),startPoint:start.startPoint};
      const result=await runMapStage(render,{stage:'collisions',startRegionId:start.startRegionId,initialPoint:start.startPoint,
       mode:p.design.collisionMode,notes:p.design.collisionNotes,regions:graph.regions,connections:graph.connections,images},signal,imageBytes);
      if(!mapFields(result,['colliders'])||!Array.isArray(result.colliders)||
         (p.design.collisionMode==='none'&&result.colliders.length!==0)||
         !result.colliders.every(c=>mapFields(c,['id','regionId','rect','shape','rotation'])))throw Error('MAP_COLLIDERS_INVALID');
      graph={...graph,colliders:result.colliders};
      if(!validMapGraph(graph))throw Error('MAP_COLLIDERS_INVALID');
     }else{
      const result=await runMapStage(render,{stage:'portals',intent:p.intent,notes:p.design?.portalNotes??'',
       startRegionId:start.startRegionId,regions:graph.regions,connections:graph.connections,images},signal,imageBytes);
      graph=applyMapCoordinates(graph,result);
      if(stageIndex===1)part=result;
     }
    }
   }
   signal?.throwIfAborted();
   if(part)resultRef=stageRef(part);
   else{
    resultRef='binari/'+mapRoot+'/'+(p.mapId??officialRequestId)+'/'+((p.expectedRevision??0)+1)+'.json';
    const receipt=await pds.publishMap(claim,graph);
    if(!mapFields(receipt,['ok','resultRef'])||receipt.ok!==true||receipt.resultRef!==resultRef)throw Error('MAP_SAVE_NOT_CONFIRMED');
   }
   db.exec('BEGIN IMMEDIATE');
   try{
    const n=db.prepare(`UPDATE workflow_tasks SET status='completed',result_ref=?,updated_at=?${part?',map_part_json=?':''}
     WHERE ${immutable} AND status='running' AND updated_at=? AND EXISTS (SELECT 1 FROM workflow_runs r WHERE
     r.official_request_id=workflow_tasks.official_request_id AND r.state='planned' AND r.reason IS NULL AND r.payload_sha256=?)`)
     .run(resultRef,Math.max(Date.now(),claim.claimStamp+1),...(part?[mapCanonical(part)]:[]),...scope,claim.claimStamp,claim.payloadSha256);
    if(n.changes!==1){db.exec('ROLLBACK');return {state:'not_claimable',taskId};}
    db.exec('COMMIT');
   }catch(error){db.exec('ROLLBACK');throw error;}
   return {state:'completed',taskId,resultRef};
  }catch(e){
   const failed=markWorkflowTaskFailed(db,{...claim,workerType:'document'});
   return {state:failed?'failed':'not_claimable',taskId,code:/^MAP_[A-Z_]+$/.test(e?.message)?e.message:'MAP_WORKFLOW_FAILED'};
  }
 }finally{db.close();}
}
