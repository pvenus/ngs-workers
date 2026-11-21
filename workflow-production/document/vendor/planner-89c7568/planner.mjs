import {DatabaseSync} from 'node:sqlite';
import {canonical,sha,validateOneStop,replaceSchemaVersion} from './envelope.mjs';

export const directions=Object.freeze(['n','ne','e','se','s','sw','w','nw']);
const rowKeys=['officialRequestId','projectId','requestType','schemaVersion','payload','payloadSha256','status','registeredAt'];
export const exact=(value,keys)=>value!==null&&typeof value==='object'&&!Array.isArray(value)&&Object.keys(value).length===keys.length&&keys.every(k=>Object.hasOwn(value,k));
const identity=/^request\.official\.[a-f0-9]{64}$/;
const ensure=(v,code)=>{if(!v)throw Error(code);};

export function checkRow(row){
 ensure(exact(row,rowKeys)&&typeof row.officialRequestId==='string'&&identity.test(row.officialRequestId)&&row.requestType==='npc.workflow'&&row.projectId==='binari'&&
   row.status==='registered'&&Number.isSafeInteger(row.registeredAt)&&row.registeredAt>=0&&
   typeof row.payloadSha256==='string'&&/^[a-f0-9]{64}$/.test(row.payloadSha256),'WORKFLOW_ROW_INVALID');
 const payload=validateOneStop(row.payload,row.projectId);
 ensure(row.payload.schemaVersion===row.schemaVersion&&sha(payload)===row.payloadSha256,'WORKFLOW_PAYLOAD_BINDING_CHANGED');
 return payload;
}

export function buildPlan(row){
 const payload=checkRow(row);
 // The currently approved DAG covers only the noncombat workflow.
 if(row.payload.includeCombat)throw Error('WORKFLOW_COMBAT_NOT_SUPPORTED');
 const npcId=row.payload.character.npcId,replace=row.schemaVersion===replaceSchemaVersion;
 const tasks=[];
 const id=key=>'workflow.task.'+sha(canonical([row.officialRequestId,key]));
 const add=(key,type,workerType,dep,extra={})=>{
  ensure(dep===null||tasks.some(t=>t.key===dep),'WORKFLOW_DEPENDENCY_NOT_EARLIER');
  tasks.push({id:id(key),key,type,workerType,dependsOn:dep===null?null:id(dep),input:{npcId,...extra}});
 };
 if(replace)add('delete','npc.delete-folder','file',null,{scope:'exact-npc-folder',preserveRequestHistory:true,noOldDataReuse:true});
 add('preset','npc.preset','document',replace?'delete':null,{mode:replace?'fresh-replacement':'new',noOldDataReuse:true});
 add('planning_representative','npc.plan-representative','document','preset');
 add('portrait_representative','npc.portrait-representative','image','planning_representative');
 add('planning_idle','npc.plan-idle','document','planning_representative');
 add('portrait_idle_reference','npc.portrait-idle-reference','image','planning_idle');
 for(const direction of directions){
  const portrait='portrait_'+direction;
  add(portrait,'npc.portrait-direction','image','portrait_idle_reference',{direction});
  for(const motion of ['walk','run']){
   const animation='animation_'+motion+'_'+direction;
   add(animation,'npc.animation-native-png','animation',portrait,{direction,motion,output:'native-png'});
   add('frames_'+motion+'_'+direction,'npc.frames-postprocess','postprocess',animation,{direction,motion,input:'native-png'});
  }
 }
 return {payload,tasks};
}

export class PlannerStore{
 constructor(path){
  this.db=new DatabaseSync(path);
  const names=this.db.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").all();
  if(names.some(({name})=>!['workflow_runs','workflow_tasks'].includes(name))){this.db.close();throw Error('WORKFLOW_DATABASE_NOT_DEDICATED');}
  this.db.exec(`PRAGMA foreign_keys=ON; PRAGMA busy_timeout=5000;
  CREATE TABLE IF NOT EXISTS workflow_runs(
    official_request_id TEXT PRIMARY KEY NOT NULL, project_id TEXT NOT NULL,
    request_type TEXT NOT NULL, schema_version TEXT NOT NULL, payload TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL, registered_at INTEGER NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('awaiting_rs','planned','failed','not_claimable')),
    updated_at INTEGER NOT NULL, reason TEXT
  ) STRICT;
  CREATE TABLE IF NOT EXISTS workflow_tasks(
    task_id TEXT PRIMARY KEY NOT NULL, official_request_id TEXT NOT NULL,
    task_key TEXT NOT NULL, task_type TEXT NOT NULL,
    worker_type TEXT NOT NULL CHECK(worker_type IN ('file','document','image','animation','postprocess')),
    task_input TEXT NOT NULL, result_ref TEXT, updated_at INTEGER NOT NULL,
    depends_on_task_id TEXT, status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','running','completed','failed')),
    UNIQUE(official_request_id,task_key), UNIQUE(official_request_id,task_id),
    CHECK(depends_on_task_id IS NULL OR depends_on_task_id<>task_id),
    FOREIGN KEY (official_request_id) REFERENCES workflow_runs(official_request_id),
    FOREIGN KEY (official_request_id,depends_on_task_id) REFERENCES workflow_tasks(official_request_id,task_id)
  ) STRICT;`);
 }
 stage(row){
  const plan=buildPlan(row);
  this.db.exec('BEGIN IMMEDIATE');
  try{
   const prior=this.db.prepare('SELECT * FROM workflow_runs WHERE official_request_id=?').get(row.officialRequestId);
   if(prior){
    ensure(prior.project_id===row.projectId&&prior.request_type===row.requestType&&prior.schema_version===row.schemaVersion&&
      prior.payload===plan.payload&&prior.payload_sha256===row.payloadSha256&&prior.registered_at===row.registeredAt,'WORKFLOW_REPLAY_CHANGED');
   }else{
    this.db.prepare("INSERT INTO workflow_runs VALUES(?,?,?,?,?,?,?,'awaiting_rs',?,?)")
     .run(row.officialRequestId,row.projectId,row.requestType,row.schemaVersion,plan.payload,row.payloadSha256,row.registeredAt,Date.now(),null);
    const stmt=this.db.prepare('INSERT INTO workflow_tasks(task_id,official_request_id,task_key,task_type,worker_type,task_input,depends_on_task_id,updated_at) VALUES(?,?,?,?,?,?,?,?)');
    for(const task of plan.tasks)stmt.run(task.id,row.officialRequestId,task.key,task.type,task.workerType,canonical(task.input),task.dependsOn,Date.now());
   }
   this.db.exec('COMMIT');return prior===undefined;
  }catch(e){this.db.exec('ROLLBACK');throw e;}
 }
 pending(){return this.db.prepare("SELECT official_request_id FROM workflow_runs WHERE state='awaiting_rs' ORDER BY registered_at,official_request_id").all();}
 acknowledge(id){return this.db.prepare("UPDATE workflow_runs SET state='planned',updated_at=? WHERE official_request_id=? AND state='awaiting_rs'").run(Date.now(),id).changes;}
 conflict(id){this.db.prepare("UPDATE workflow_runs SET state='not_claimable',updated_at=?,reason='WORKFLOW_RS_STATUS_CONFLICT' WHERE official_request_id=? AND state='awaiting_rs'").run(Date.now(),id);}
 close(){this.db.close();}
}

export async function pollOnce(store,rs){
 let cursor=null;const seen=new Set();let staged=0,invalid=0,unsupported=0,acknowledged=0,conflicts=0,pollError=false;
 try{
  do{
   const args=cursor===null?{limit:8}:{limit:8,cursor};
   const page=await rs.call('request_workflow_list',args);
   ensure(exact(page,['requests','nextCursor'])&&Array.isArray(page.requests)&&page.requests.length<=8&&
    (page.nextCursor===null||typeof page.nextCursor==='string'&&page.nextCursor.length>0&&!seen.has(page.nextCursor)),'WORKFLOW_RS_PAGE_INVALID');
   for(const row of page.requests){
    try{staged+=Number(store.stage(row));}
    catch(e){
     if(e.message==='WORKFLOW_COMBAT_NOT_SUPPORTED'){unsupported++;continue;}
     if(!['WORKFLOW_ROW_INVALID','WORKFLOW_PAYLOAD_BINDING_CHANGED','WORKFLOW_REPLAY_CHANGED','NGS_ONE_STOP_PAYLOAD_INVALID'].includes(e.message))throw e;
     invalid++;
    }
   }
   cursor=page.nextCursor;if(cursor!==null)seen.add(cursor);
  }while(cursor!==null);
 }catch{pollError=true;}
 // Reconcile persisted ACKs independently of the list. A successful but lost
 // response may already have removed the row from RS's registered-only feed.
 for(const {official_request_id:id} of store.pending()){
  const status='planned';
  try{
   const ack=await rs.call('request_update_status',{officialRequestId:id,status});
   ensure(exact(ack,['officialRequestId','status'])&&ack.officialRequestId===id&&ack.status===status,'WORKFLOW_RS_ACK_INVALID');
   acknowledged+=store.acknowledge(id);
  }catch(e){
   if(e.rsStatus===409){store.conflict(id);conflicts++;}
  }
 }
 return {staged,invalid,unsupported,acknowledged,conflicts,pending:store.pending().length,pollError};
}
