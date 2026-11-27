import {createHash} from 'node:crypto';
import {readFile,realpath,stat} from 'node:fs/promises';
import {spawn} from 'node:child_process';
import {dirname,isAbsolute} from 'node:path';

const dotnet='/opt/homebrew/bin/dotnet',limit=64*1024;
const failed=()=>Error('ASSEMBLY_CSHARP_VALIDATION_FAILED');

// Host configuration only. Model output is bounded stdin; it cannot supply a
// command, a file path, arguments, or an asserted validation result.
export function createAssemblyValidator({env=process.env}={}){
 const path=env.WORKFLOW_ASSEMBLY_VALIDATOR_DLL, pin=env.WORKFLOW_ASSEMBLY_VALIDATOR_SHA256;
 return async (definition,{signal}={})=>{
  if(typeof path!=='string'||!isAbsolute(path)||!path.endsWith('.dll')||!/^[a-f0-9]{64}$/u.test(pin??''))
   throw Error('ASSEMBLY_CSHARP_VALIDATOR_UNAVAILABLE');
  try{
   const info=await stat(path);
   if(await realpath(path)!==path||!info.isFile()||info.size<1||info.size>16*1024*1024||
      (info.mode&0o022)!==0||createHash('sha256').update(await readFile(path)).digest('hex')!==pin)
    throw Error('untrusted');
  }catch{throw Error('ASSEMBLY_CSHARP_VALIDATOR_UNAVAILABLE');}
  const input=Buffer.from(JSON.stringify(definition));
  if(input.length<2||input.length>limit||signal?.aborted)throw failed();
  return new Promise((resolve,reject)=>{
   const child=spawn(dotnet,[path],{cwd:dirname(path),shell:false,
    env:{HOME:env.HOME??'/tmp',DOTNET_NOLOGO:'1',DOTNET_CLI_TELEMETRY_OPTOUT:'1'},stdio:['pipe','pipe','ignore']});
   let output='',done=false;
   const finish=(error,value)=>{
    if(done)return;
    done=true;clearTimeout(timer);signal?.removeEventListener('abort',stop);
    if(error){child.kill('SIGKILL');reject(error);}else resolve(value);
   };
   const stop=()=>finish(failed());
   const timer=setTimeout(stop,12000);timer.unref();
   signal?.addEventListener('abort',stop,{once:true});
   if(signal?.aborted){stop();return;}
   child.on('error',stop);child.stdin.on('error',stop);
   child.stdout.setEncoding('utf8');
   child.stdout.on('data',text=>{output+=text;if(output.length>256)stop();});
   child.on('close',(code,term)=>{
    if(code!==0||term!==null||!/^\{"ok":true\}(?:\r?\n)?$/u.test(output)){stop();return;}
    finish(null,true);
   });
   child.stdin.end(input);
  });
 };
}
