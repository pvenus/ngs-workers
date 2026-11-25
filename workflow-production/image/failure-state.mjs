import {setTimeout as delay} from 'node:timers/promises';

// Retry only a transient DB write lock, not model execution or remote writes.
// The caller's exact claim/CAS is unchanged. A false CAS is never retried.
export async function settleFailure(write,{sleep=delay}={}){
 const backoff=[100,300,1000,2500,5000];
 for(let i=0;;i++){
  try{return write();}
  catch(error){
   const sqlite=Number.isInteger(error?.errcode)?error.errcode&255:null;
   if(![5,6].includes(sqlite)||i===backoff.length)throw error;
   await sleep(backoff[i]);
  }
 }
}
