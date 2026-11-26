import {fork} from 'node:child_process';

export const imagePoolSize=2;

/** Run one process (and one resident Codex bridge) per image lane. The
 * launchd-facing supervisor owns the single OPS heartbeat for both lanes. */
export function startImagePool({entry,args,onReady,emit=()=>{},spawn=fork,
 laneCount=imagePoolSize,restartDelay=1000}){
 const lanes=Array.from({length:laneCount},(_,i)=>({id:i+1,child:null,ready:false,timer:null}));
 let stopping=false,healthy=false,finish;
 const done=new Promise(resolve=>{finish=resolve;});
 const health=()=>{
  const ready=!stopping&&lanes.every(l=>l.child&&l.ready);
  if(ready!==healthy){healthy=ready;onReady(ready);}
  if(stopping&&lanes.every(l=>!l.child))finish();
 };
 function open(lane){
  if(stopping)return;
  const retire=()=>{
   if(lane.child!==child)return;
   lane.child=null;lane.ready=false;
   health();
   if(!stopping)retry();
  };
  const retry=()=>{
   if(stopping)return;
   lane.timer=setTimeout(()=>{lane.timer=null;open(lane);},restartDelay);
  };
  let child;
  try{
   child=spawn(entry,args,{env:{...process.env,NGS_IMAGE_POOL_LANE:String(lane.id)},stdio:['ignore','inherit','inherit','ipc']});
  }catch{
   emit({event:'spawn-failed',lane:lane.id});retry();return;
  }
  lane.child=child;lane.ready=false;
  child.on('message',m=>{
   if(lane.child!==child||stopping||m?.type!=='image-lane-health'||typeof m.ready!=='boolean')return;
   lane.ready=m.ready;health();
  });
  child.once('error',()=>{if(!child.pid)retire();});
  child.once('disconnect',()=>{if(lane.child===child){lane.ready=false;health();}});
  child.once('exit',(code,signal)=>{
   if(lane.child!==child)return;
   emit({event:'lane-exit',lane:lane.id,pid:child.pid,code,signal});
   retire();
  });
  emit({event:'lane-start',lane:lane.id,pid:child.pid});
 }
 for(const lane of lanes)open(lane);
 return {
  done,
  stop(){
   if(stopping)return done;
   stopping=true;
   for(const lane of lanes){
    clearTimeout(lane.timer);lane.timer=null;
    if(lane.child&&lane.child.connected)lane.child.send({type:'image-pool-drain'},error=>{
     if(error)lane.child?.kill('SIGTERM');
    });
    else lane.child?.kill('SIGTERM');
   }
   health();return done;
  },
 };
}
