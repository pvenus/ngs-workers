import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {homedir} from 'node:os';

// One outstanding request; stack and content never go to operational logs.
export function createCodexSessionClient({python='/opt/homebrew/bin/python3.12',script,args=[]}) {
 let child, active;
 const close=()=>{if(child){child.kill('SIGTERM');child.stdin.end();child.unref();child=null;}};
 function start(){
  const p=spawn(python,['-I','-B',fileURLToPath(script),...args],{
   env:{HOME:homedir(),PATH:'/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin',...(process.env.TMPDIR?{TMPDIR:process.env.TMPDIR}:{})},
   stdio:['pipe','pipe','inherit'],
  });
  child=p;
  p.on('error',()=>{if(active?.child===p)active.finish('start_failed');});
  p.stdin.on('error',()=>{if(active?.child===p)active.finish('disconnected');});
  p.on('close',()=>{if(child===p)child=null;if(active?.child===p)active.finish('disconnected');});
  return p;
 }
 return {
  close,
  call(input,{signal,timeoutMs,maxReply=16*1024*1024}){
   if(signal?.aborted)return Promise.reject(Error('cancelled'));
   if(active)return Promise.reject(Error('concurrent'));
   return new Promise((resolve,reject)=>{
    const p=child??start(), chunks=[];
    let bytes=0,done=false;
    const cleanup=()=>{
     clearTimeout(timer);signal?.removeEventListener('abort',abort);
     p.stdout.off('data',read);active=null;
     p.stdout.unref();p.stdin.unref();p.unref();
    };
    const finish=(reason,answer)=>{
     if(done)return;done=true;cleanup();
     if(reason){if(child===p)close();reject(Error(reason));}
     else if(answer?.ok===true)resolve(answer.value);
     else if(answer?.ok===false&&typeof answer.reason==='string'&&/^[a-z][a-z_0-9]{0,39}$/u.test(answer.reason))
      reject(Error(answer.reason));
     else{if(child===p)close();reject(Error('invalid_output'));}
    };
    const abort=()=>finish('cancelled');
    const read=chunk=>{
     bytes+=chunk.length;
     if(bytes>maxReply){finish('output_limit');return;}
     chunks.push(chunk);
     if(chunk[chunk.length-1]===10){
      try{finish(null,JSON.parse(Buffer.concat(chunks,bytes).toString('utf8')));}
      catch{finish('invalid_output');}
     }
    };
    const timer=setTimeout(()=>finish('timeout'),timeoutMs);
    active={child:p,finish};
    p.ref();p.stdout.ref();p.stdin.ref();p.stdout.on('data',read);
    signal?.addEventListener('abort',abort,{once:true});
    if(signal?.aborted){abort();return;}
    p.stdin.write(JSON.stringify(input)+'\n');
   });
  }
 };
}
