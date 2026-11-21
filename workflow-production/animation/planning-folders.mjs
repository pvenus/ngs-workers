import {createHash} from 'node:crypto';
import {constants} from 'node:fs';
import {lstat,mkdir,mkdtemp,open,realpath,rm} from 'node:fs/promises';
import {request} from 'node:http';
import {tmpdir} from 'node:os';
import {join} from 'node:path';

const defaultOrigin='http://127.0.0.1:8030/mcp', protocolVersion='2025-06-18';
const maxFiles=32768,maxChunk=1024*1024,maxFile=64*maxChunk,maxManifestResponse=64*maxChunk;
const fault=code=>Error(code);
const wire=()=>fault('PLANNING_FOLDER_MCP_UNAVAILABLE');
const invalid=()=>fault('PLANNING_FOLDER_RESPONSE_INVALID');
const digest=/^[a-f0-9]{64}$/;
// Same media suffix exclusions as the document planning input boundary. Reject
// at inventory time so binary media is never downloaded as planning context.
const media=/\.(?:png|jpe?g|gif|webp|bmp|avif|heic|ico|tiff?|svg|mp4|mov|avi|wav|mp3|flac|webm|zip|pdf)$/i;
const npcPattern=/^(?:npc|character)\.[a-z0-9]+(?:[-._][a-z0-9]+)*$/;
const hasKeys=(v,list)=>v&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).sort().join(',')===list;
const order=(a,b)=>Buffer.compare(Buffer.from(a),Buffer.from(b));
function validPath(path){
 if(typeof path!=='string'||!path||Buffer.byteLength(path)>4096||/[\\\u0000-\u001f\u007f]/u.test(path))return false;
 const parts=path.split('/');
 return parts.length<=64&&parts.every(p=>p&&!p.startsWith('.')&&Buffer.byteLength(p)<=255);
}

function loopbackCall(url,signal){
 const endpoint=new URL(url);
 if(endpoint.protocol!=='http:'||endpoint.hostname!=='127.0.0.1'||!endpoint.port||url!==endpoint.origin+'/mcp')throw wire();
 let seq=0,ready;
 const post=(body,limit,notification=false,handshake=false)=>new Promise((resolve,reject)=>{
  const bytes=Buffer.from(JSON.stringify(body));
  const timeout=AbortSignal.timeout(60_000);
  const req=request(endpoint,{method:'POST',agent:false,signal:signal?AbortSignal.any([signal,timeout]):timeout,headers:{
   Accept:'application/json, text/event-stream','Content-Type':'application/json','Content-Length':String(bytes.length),
   ...(handshake?{}:{'MCP-Protocol-Version':protocolVersion}),
  }},res=>{
   res.once('error',()=>reject(wire()));
   if(notification){
    if(res.statusCode!==202){res.destroy();reject(wire());return;}
    res.resume();res.once('end',()=>resolve());return;
   }
   if(res.statusCode!==200||!/^application\/json(?:\s*;|$)/i.test(res.headers['content-type']??'')){res.destroy();reject(wire());return;}
   const chunks=[];let size=0;
   res.on('data',chunk=>{
    size+=chunk.length;
    if(size>limit){res.destroy();reject(wire());return;}
    chunks.push(chunk);
   });
   res.once('aborted',()=>reject(wire()));
   res.once('end',()=>{
    try{
     const v=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(Buffer.concat(chunks,size)));
     if(v?.jsonrpc!=='2.0'||v.id!==body.id||v.error||!v.result)throw wire();
     resolve(v.result);
    }catch{reject(wire());}
   });
  });
  req.once('error',()=>reject(wire()));req.end(bytes);
 });
 const rpc=(method,params,limit,handshake)=>post({jsonrpc:'2.0',id:++seq,method,params},limit,false,handshake);
 return async(name,args,limit=3*maxChunk)=>{
  if(!ready)ready=(async()=>{
   const info=await rpc('initialize',{protocolVersion,capabilities:{},clientInfo:{name:'workflow-planning-folders',version:'1'}},8192,true);
   if(info.protocolVersion!==protocolVersion||!info.capabilities?.tools)throw wire();
   await post({jsonrpc:'2.0',method:'notifications/initialized'},0,true);
  })();
  await ready;
  const v=await rpc('tools/call',{name,arguments:args},limit);
  if(v.isError||!v.structuredContent||typeof v.structuredContent!=='object')throw wire();
  return v.structuredContent;
 };
}

async function inventory(call,scope){
 const raw=await call('npc_folders_compare',{scope,files:[]},maxManifestResponse);
 if(!hasKeys(raw,'groups,scope')||raw.scope!==scope||!Array.isArray(raw.groups)||raw.groups.length>maxFiles)throw invalid();
 const folders=new Set(),all=new Map();
 for(const group of raw.groups){
  const f=group?.folder;
  if(!hasKeys(group,'files,folder')||(f!==''&&(!validPath(f)||f.includes('/')))||folders.has(f)||
    !Array.isArray(group.files)||group.files.length===0||all.size+group.files.length>maxFiles)throw invalid();
  folders.add(f);
  for(const row of group.files){
   if(!hasKeys(row,'path,sha256,size')||!validPath(row.path)||typeof row.sha256!=='string'||!digest.test(row.sha256)||
    !Number.isSafeInteger(row.size)||row.size<0||row.size>maxFile||all.has(row.path)||
    (row.path.includes('/')?row.path.split('/')[0]:'')!==f)throw invalid();
   if(media.test(row.path))throw fault('PLANNING_FOLDER_MEDIA_NOT_ALLOWED');
   all.set(row.path,{scope,path:row.path,sha256:row.sha256,size:row.size});
  }
 }
 return [...all.values()].sort((a,b)=>order(a.path,b.path));
}

async function assertPrivateDirectory(path){
 const st=await lstat(path);
 if(!st.isDirectory()||st.uid!==process.getuid()||(st.mode&0o077)||await realpath(path)!==path)
  throw fault('PLANNING_FOLDER_LOCAL_PATH_INVALID');
}

async function localFile(scopeRoot,path){
 const parts=path.split('/'),name=parts.pop();
 let target=scopeRoot;
 await assertPrivateDirectory(target);
 for(const part of parts){
  target=join(target,part);
  try{await mkdir(target,{mode:0o700});}catch(e){if(e.code!=='EEXIST')throw e;}
  await assertPrivateDirectory(target);
 }
 const file=join(target,name);
 return {file,handle:await open(file,constants.O_WRONLY|constants.O_CREAT|constants.O_EXCL|constants.O_NOFOLLOW,0o600)};
}

async function download(call,scopeRoot,descriptor,signal){
 const {scope,path,sha256,size}=descriptor;
 const {file,handle}=await localFile(scopeRoot,path),hash=createHash('sha256');
 try{
  let offset=0;
  while(true){
   signal?.throwIfAborted();
   const v=await call('npc_folder_file_chunk',{scope,path,sha256,offset});
   if(!hasKeys(v,'base64,nextOffset,offset,path,scope,sha256,size')||v.scope!==scope||v.path!==path||v.sha256!==sha256||
    v.size!==size||v.offset!==offset||typeof v.base64!=='string'||v.base64.length>4*Math.ceil(maxChunk/3))throw invalid();
   const bytes=Buffer.from(v.base64,'base64'),end=offset+bytes.length;
   if(bytes.length>maxChunk||bytes.toString('base64')!==v.base64||end>size||
    v.nextOffset!==(end===size?null:end)||(!bytes.length&&end!==size))throw invalid();
   await handle.writeFile(bytes);
   hash.update(bytes);
   if(end===size)break;
   offset=end;
  }
  if(hash.digest('hex')!==sha256)throw fault('PLANNING_FOLDER_DIGEST_MISMATCH');
 }finally{await handle.close();}
 return Object.freeze({...descriptor,file});
}

export function createPlanningFolderDownloader({call,url=defaultOrigin,tempParent=tmpdir()}={}){
 return async function withPlanningSnapshot({npcId,signal}={},use){
  if(typeof npcId!=='string'||npcId.length>128||!npcPattern.test(npcId)||typeof use!=='function')
   throw fault('PLANNING_FOLDER_TARGET_INVALID');
  signal?.throwIfAborted();
  const mcp=call??loopbackCall(url,signal);
  const scopes=['binari/concept','binari/npcs/'+npcId+'/planning'];
  const root=await mkdtemp(join(await realpath(tempParent),'workflow-planning-folders-'));
  try{
   const initial=[];
   for(const scope of scopes){signal?.throwIfAborted();initial.push(await inventory(mcp,scope));}
   const files=[];
   for(let i=0;i<scopes.length;i++){
    const scopeRoot=join(root,i===0?'concept':'planning');
    await mkdir(scopeRoot,{mode:0o700});
    for(const item of initial[i])files.push(await download(mcp,scopeRoot,item,signal));
   }
   for(let i=0;i<scopes.length;i++){
    signal?.throwIfAborted();
    if(JSON.stringify(await inventory(mcp,scopes[i]))!==JSON.stringify(initial[i]))throw fault('PLANNING_FOLDER_SNAPSHOT_CHANGED');
   }
   signal?.throwIfAborted();
   return await use(Object.freeze({root,files:Object.freeze(files)}));
  }finally{await rm(root,{recursive:true,force:true});}
 };
}
export const withPlanningSnapshot=createPlanningFolderDownloader();
