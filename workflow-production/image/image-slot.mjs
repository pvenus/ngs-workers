import {lstatSync,mkdirSync,mkdtempSync,openSync,closeSync,realpathSync,rmSync,unlinkSync,writeFileSync} from 'node:fs';
import {homedir} from 'node:os';
import {join} from 'node:path';

export function imageSlotIdentity(value='1'){
 if(value!=='1'&&value!=='2')throw Error('IMAGE_WORKER_SLOT_INVALID');
 return Object.freeze({slot:value,serverName:value==='1'?'image-worker':'image-worker-2'});
}
function privateDirectory(path){
 mkdirSync(path,{recursive:true,mode:0o700});
 const f=lstatSync(path);
 if(!f.isDirectory()||f.uid!==process.getuid()||(f.mode&0o077)!==0||realpathSync(path)!==path)
  throw Error('IMAGE_WORKER_SLOT_ROOT_UNSAFE');
 return path;
}
export function createImageSlot(value,{base=join(homedir(),'Workspaces','Workers','runtime','image-worker-slots')}={}){
 const {slot,serverName}=imageSlotIdentity(value), root=privateDirectory(join(privateDirectory(base),slot));
 const tmp=mkdtempSync(join(privateDirectory(join(root,'scratch')),'worker-'));
 const lease=join(root,'active'),pause=join(root,'pause');
 const pathPresent=p=>{try{lstatSync(p);return true;}catch(e){if(e.code==='ENOENT')return false;throw e;}};
 return Object.freeze({slot,serverName,tmp,
  cleanup(){rmSync(tmp,{recursive:true,force:true});},
  tryEnter(){
   // A pause acquired before or during the exclusive lease prohibits
   // admission, including the check-to-claim interval.
   if(pathPresent(pause))return null;
   let fd;
   try{fd=openSync(lease,'wx',0o600);}catch(e){if(e.code==='EEXIST')return null;throw e;}
   let released=false;
   const release=()=>{if(!released){released=true;closeSync(fd);unlinkSync(lease);}};
   try{
    writeFileSync(fd,String(process.pid)+'\n');
    if(pathPresent(pause)){release();return null;}
    return release;
   }catch(e){release();throw e;}
  }
 });
}
