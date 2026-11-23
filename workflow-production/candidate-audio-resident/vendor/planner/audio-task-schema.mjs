/** Narrow lossless extension of the original workflow task worker CHECK. */
export const WORKFLOW_TASK_MAX_RETRIES=3;
export const RETRY_COUNT_COLUMN=`retry_count INTEGER NOT NULL DEFAULT 0 CHECK(retry_count BETWEEN 0 AND ${WORKFLOW_TASK_MAX_RETRIES})`;
const live='workflow_tasks',stage='workflow_tasks_audio_stage';
const oldWorkers="'file','document','image','animation','postprocess'";
const columns='task_id,official_request_id,task_key,task_type,worker_type,task_input,result_ref,updated_at,depends_on_task_id,status,retry_count';
function ddl(table,{audio=true,retry=true,ifMissing=false}={}){
 return `CREATE TABLE ${ifMissing?'IF NOT EXISTS ':''}${table}(
    task_id TEXT PRIMARY KEY NOT NULL, official_request_id TEXT NOT NULL,
    task_key TEXT NOT NULL, task_type TEXT NOT NULL,
    worker_type TEXT NOT NULL CHECK(worker_type IN (${oldWorkers}${audio?",'audio'":''})),
    task_input TEXT NOT NULL, result_ref TEXT, updated_at INTEGER NOT NULL,
    depends_on_task_id TEXT, status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','running','completed','failed')),
    ${retry?RETRY_COUNT_COLUMN+',':''}
    UNIQUE(official_request_id,task_key), UNIQUE(official_request_id,task_id),
    CHECK(depends_on_task_id IS NULL OR depends_on_task_id<>task_id),
    FOREIGN KEY (official_request_id) REFERENCES workflow_runs(official_request_id),
    FOREIGN KEY (official_request_id,depends_on_task_id) REFERENCES ${table}(official_request_id,task_id)
  ) STRICT`;
}
export const createWorkflowTasksSql=ddl(live,{ifMissing:true});
const normal=sql=>sql?.replace(/"workflow_tasks"/g,live).replace(/'[^']*'|\s+/g,token=>token[0]==="'"?token:'');
const legacy=normal(ddl(live,{audio:false})),current=normal(ddl(live));
const failure=()=>Error('WORKFLOW_AUDIO_TASK_SCHEMA_UNKNOWN');

export function migrateWorkflowAudioTask(db){
 db.exec('BEGIN IMMEDIATE');
 try{
  const sql=db.prepare("SELECT sql FROM sqlite_schema WHERE type='table' AND name=?").get(live)?.sql;
  if(normal(sql)===current){db.exec('COMMIT');return false;}
  if(normal(sql)!==legacy||
     db.prepare("SELECT count(*) AS n FROM sqlite_schema WHERE (type IN ('index','trigger') AND tbl_name=? AND sql IS NOT NULL) OR type='view'").get(live).n!==0||
     db.prepare(`PRAGMA foreign_key_check(${live})`).all().length)throw failure();
  // Every task retains its explicit rowid as well as its own state and IDs.
  // Defer checks until all self dependencies are copied and the old table is gone.
  db.exec(`PRAGMA defer_foreign_keys=ON; ${ddl(stage)};
    INSERT INTO ${stage}(rowid,${columns}) SELECT rowid,${columns} FROM ${live};`);
  for(const [a,b] of [[live,stage],[stage,live]]){
   if(db.prepare(`SELECT rowid,${columns} FROM ${a} EXCEPT SELECT rowid,${columns} FROM ${b} LIMIT 1`).get())
    throw failure();
  }
  if(db.prepare(`PRAGMA foreign_key_check(${stage})`).all().length)throw failure();
  db.exec(`DROP TABLE ${live}; ALTER TABLE ${stage} RENAME TO ${live};`);
  if(db.prepare('PRAGMA foreign_key_check').all().length)throw failure();
  db.exec('COMMIT');return true;
 }catch(error){db.exec('ROLLBACK');throw error;}
}
