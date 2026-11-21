import {spawn} from 'node:child_process';
import {mkdtemp,rm,realpath} from 'node:fs/promises';
import {basename,dirname,join} from 'node:path';
import {tmpdir} from 'node:os';

export const warmIdleMs=300_000;
const closeMs=50_000; // Existing TERM/KILL/group absence windows, with margin.
function failure(code,joined=false){const error=Error(code);error.rendererJoined=joined;return error;}
const hasKeys=(value,keys)=>value&&typeof value==='object'&&!Array.isArray(value)&&
 Object.keys(value).sort().join(',')===keys;

export function spawnAnimationChannel({py,helper,provider,binding,root}){
 const child=spawn(py,['-I','-B',helper,provider,binding,root,'--serve'],{stdio:['pipe','pipe','pipe']});
 let buffer='',waiting,exited=false,closedResolve;
 const exitedPromise=new Promise(resolve=>{closedResolve=resolve;});
 const lose=()=>{
  const current=waiting;waiting=undefined;
  current?.reject(failure('ANIMATION_PROCESS_STOP_UNCONFIRMED'));
 };
 child.stdout.setEncoding('utf8');
 child.stdout.on('data',part=>{
  buffer+=part;
  if(buffer.length>256_000){buffer='';lose();return;}
  const end=buffer.indexOf('\n');if(end<0)return;
  const line=buffer.slice(0,end),tail=buffer.slice(end+1);buffer='';
  const current=waiting;waiting=undefined;
  if(!current||tail){current?.reject(failure('ANIMATION_WARM_PROTOCOL'));return;}
  try{current.resolve(JSON.parse(line));}catch{current.reject(failure('ANIMATION_WARM_PROTOCOL'));}
 });
 child.stderr.resume(); // Never copy model/host output into task logs.
 child.stdin.on('error',lose);child.on('error',lose);
 child.on('close',code=>{exited=true;lose();closedResolve(code);});
 function command(request,signal){
  return new Promise((resolve,reject)=>{
   if(waiting||exited)return reject(failure('ANIMATION_PROCESS_STOP_UNCONFIRMED'));
   const abort=()=>{try{child.kill('SIGTERM');}catch{lose();}};
   const unbind=()=>signal?.removeEventListener('abort',abort);
   waiting={resolve:value=>{unbind();resolve(value);},reject:error=>{unbind();reject(error);}};
   signal?.addEventListener('abort',abort,{once:true});
   // Write before signalling a cancellation so an in-flight claim settles.
   child.stdin.write(JSON.stringify(request)+'\n',error=>{if(error)lose();});
   if(signal?.aborted)abort();
  });
 }
 return {command,alive:()=>!exited,exited:exitedPromise,end:()=>child.stdin.end()};
}

function bounded(promise,ms){
 let timer;const deadline=new Promise((_,reject)=>{timer=setTimeout(()=>reject(failure('ANIMATION_PROCESS_STOP_UNCONFIRMED')),ms);});
 return Promise.race([promise,deadline]).finally(()=>clearTimeout(timer));
}

/** Sequential owned engine; only a confirmed stop resets a session. */
export function createWarmAnimation({
 launch,now=()=>performance.now(),ttl=warmIdleMs,
 makeRoot=async()=>realpath(await mkdtemp(join(tmpdir(),'workflow-wan-resident-'))),
 makeJob=root=>mkdtemp(join(root,'job-')),removeRoot=root=>rm(root,{recursive:true,force:true}),closeWithin=closeMs,onIdleUnload=()=>{},
}){
 let root,channel,lastWork=null,unsafe=false,busy=false,channelEnded=false;
 const latch=()=>{
  unsafe=true;
  if(channel&&!channelEnded){channelEnded=true;try{channel.end();}catch{/* unknown stays latched */}}
 };
 const ensureSafe=()=>{
  if(unsafe||channel&&!channel.alive()){
   latch();throw failure('ANIMATION_PROCESS_STOP_UNCONFIRMED');
  }
 };
 async function cleanRoot(){
  if(root){const old=root;root=undefined;await removeRoot(old);}
 }
 async function finishConfirmed(prior){
  // A valid joined response is required AND the actual helper must exit.
  if(await bounded(prior.exited,closeWithin)!==0)throw failure('ANIMATION_PROCESS_STOP_UNCONFIRMED');
  channel=undefined;lastWork=null;
 }
 async function render(job,signal){
  ensureSafe();if(busy)throw failure('ANIMATION_WARM_BUSY');
  if(!root||dirname(job)!==root||!/^job-[A-Za-z0-9]{6}$/.test(basename(job)))throw failure('ANIMATION_WARM_JOB_SCOPE');
  if(signal.aborted)throw failure('ANIMATION_CANCELLED',!channel);
  busy=true;
  try{
   if(!channel){
    try{channel=launch(root);}
    catch{latch();throw failure('ANIMATION_PROCESS_STOP_UNCONFIRMED');}
   }
   const reply=await channel.command({op:'render',job:basename(job)},signal);
   if(hasKeys(reply,'kind,result')&&reply.kind==='rendered'&&reply.result&&typeof reply.result==='object'){
    return reply.result;
   }
   if(hasKeys(reply,'code,joined,kind')&&reply.kind==='error'&&reply.joined===true&&
    typeof reply.code==='string'&&/^[A-Z0-9_]{1,130}$/.test(reply.code)){
    await finishConfirmed(channel);throw failure(reply.code,true);
   }
   throw failure('ANIMATION_PROCESS_STOP_UNCONFIRMED');
  }catch(error){
   if(channel&&error.rendererJoined!==true)latch();
   throw error;
  }finally{busy=false;}
 }
 async function close(){
  if(busy||unsafe)return false;
  if(channel){
   try{
    const reply=await bounded(channel.command({op:'close'}),closeWithin);
    if(!hasKeys(reply,'joined,kind')||reply.kind!=='closed'||reply.joined!==true)throw failure('ANIMATION_PROCESS_STOP_UNCONFIRMED');
    await finishConfirmed(channel);
   }catch{latch();return false;}
  }
  await cleanRoot();
  return true;
 }
 return {
  cold:()=>{ensureSafe();return !channel;},
  // Only preserve a job directory if the helper may still access it.
  safeToClean:()=>!unsafe||Boolean(channel&&!channel.alive()),
  async jobDir(){ensureSafe();root??=await makeRoot();return makeJob(root);},
  render,
  workFinished(){if(channel&&!unsafe&&channel.alive())lastWork=now();},
  async idle(){
   if(!channel)return close();
   if(lastWork===null||now()-lastWork<ttl)return !unsafe;
   const closed=await close();if(closed)onIdleUnload();return closed;
  },
  idleDelay:(pollMs)=>channel&&lastWork!==null&&!unsafe?Math.max(0,Math.min(pollMs,lastWork+ttl-now())):pollMs,
  shutdown:close,
 };
}
