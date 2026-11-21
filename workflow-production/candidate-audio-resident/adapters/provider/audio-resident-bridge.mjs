/** A single strictly framed, per-worker child. Not a listener or a shared service. */
export const audioLifecycle=Symbol('bgm-resident-lifecycle');
export const warmIdleMs=300_000;
const stages=new Set(['setup','plan','render','verify','unknown']);
const providerCode=/^(?:AUDIO_HQ_|HQ_)[A-Z0-9_]{1,52}$/u;
const failure=code=>Error(code);
const failureCode='AUDIO_ONE_PROVIDER_FAILED',invalid='AUDIO_HQ_RECEIPT_INVALID';
export function createResidentBridge({
 binary,argv,env,spawnProcess,parseResult,maxResponse,idleMs=warmIdleMs,taskMs=1_200_000,
}){
 let active=null,serial=0,requesting=false,reserved=false,idleTimer,closed=false;
 function dropIdle(){clearTimeout(idleTimer);idleTimer=undefined;}
 function scheduleIdle(){
  if(idleTimer||requesting||reserved||closed||!active||active.closing)return;
  idleTimer=setTimeout(()=>{
   idleTimer=undefined;
   if(!requesting&&!reserved&&active)terminate(active,true);
  },idleMs);
 }
 function terminate(s,graceful){
  if(s.closing)return s.closed;
  s.closing=true;dropIdle();
  if(graceful){
   s.child.stdin.end();
   s.soft=setTimeout(()=>s.child.kill('SIGTERM'),5000);
  }else s.child.kill('SIGTERM');
  s.hard=setTimeout(()=>s.child.kill('SIGKILL'),30_000);
  return s.closed;
 }
 function abort(s,error){
  if(s.pending&&!s.pending.error)s.pending.error=failure(error);
  return terminate(s,false);
 }
 function open(){
  const child=spawnProcess(binary,argv,{env,stdio:['pipe','pipe','pipe']});
  const s={child,parts:[],size:0,closing:false,pending:null};
  let finalize;
  s.closed=new Promise(resolve=>{finalize=resolve;});
  child.stderr.on('data',()=>{}); // Never trust process stderr as a protocol response.
  child.stdin.on('error',()=>abort(s,failureCode));
  child.stdout.on('error',()=>abort(s,failureCode));
  child.once('error',()=>abort(s,'AUDIO_ONE_PROVIDER_UNAVAILABLE'));
  child.once('close',()=>{
   clearTimeout(s.soft);clearTimeout(s.hard);
   if(active===s){active=null;dropIdle();}
   if(s.pending){
    const p=s.pending;s.pending=null;p.clean();
    p.reject(p.error??failure(failureCode));
   }
   finalize();
  });
  child.stdout.on('data',data=>{
   if(s.closing)return;
   const at=data.indexOf(10);
   if(!s.pending||at>=0&&at!==data.length-1){void abort(s,invalid);return;}
   const chunk=at<0?data:data.subarray(0,at);
   s.size+=chunk.length;
   if(s.size>maxResponse){void abort(s,'AUDIO_ONE_OUTPUT_OVERSIZE');return;}
   s.parts.push(chunk);
   if(at<0)return;
   const p=s.pending;
   try{
    const raw=Buffer.concat(s.parts,s.size);
    const v=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(raw));
    if(!v||v.id!==p.id)throw failure(invalid);
    if(v.ok===true&&Object.keys(v).sort().join(',')==='id,ok,result'){
     const result=parseResult(v.result);
     s.pending=null;s.parts=[];s.size=0;p.clean();p.resolve(result);
    }else if(v.ok===false&&Object.keys(v).sort().join(',')==='error,id,ok,phase'&&
             typeof v.error==='string'&&providerCode.test(v.error)&&stages.has(v.phase)){
     p.error=Object.assign(failure(failureCode),{providerPhase:v.phase,providerCode:v.error});
     void terminate(s,true); // Do not let failed provider state serve another request.
    }else throw failure(invalid);
   }catch{void abort(s,invalid);}
  });
  active=s;
  return s;
 }
 async function request(bytes,signal){
  if(closed||signal?.aborted)throw failure('AUDIO_ONE_CANCELLED');
  if(requesting)throw failure('AUDIO_ONE_PROVIDER_BUSY');
  requesting=true;dropIdle();
  try{
   if(active?.closing)await active.closed;
   if(closed||signal?.aborted)throw failure('AUDIO_ONE_CANCELLED');
   let s;
   try{s=active??open();}catch{throw failure('AUDIO_ONE_PROVIDER_UNAVAILABLE');}
   return await new Promise((resolve,reject)=>{
    const id=String(++serial);
    const halt=()=>void abort(s,'AUDIO_ONE_CANCELLED');
    const timeout=setTimeout(()=>void abort(s,'AUDIO_ONE_PROVIDER_TIMEOUT'),taskMs);
    const clean=()=>{clearTimeout(timeout);signal?.removeEventListener('abort',halt);};
    s.pending={id,resolve,reject,clean};
    signal?.addEventListener('abort',halt,{once:true});
    if(signal?.aborted){halt();return;}
    const wire=Buffer.concat([Buffer.from('{"id":"'+id+'","input":'),bytes,Buffer.from('}\n')]);
    s.child.stdin.write(wire,e=>{if(e)void abort(s,failureCode);});
   });
  }finally{requesting=false;scheduleIdle();}
 }
 return {
  request,
  beginTask(){reserved=true;dropIdle();},
  endTask(){reserved=false;scheduleIdle();},
  async close(){
   closed=true;dropIdle();
   if(active){
    if(active.pending)await abort(active,'AUDIO_ONE_CANCELLED');
    else await terminate(active,true);
   }
  },
 };
}
