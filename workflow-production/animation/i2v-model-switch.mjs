/** Separate providers share one resident animation worker and one sequential model slot. */
export function createAnimationModelSwitch(wan,i2v){
 let unknown=false,busy=false;
 const error=(message,joined=false)=>Object.assign(Error(message),{rendererJoined:joined});
 function assertKnown(){
  if(unknown)throw error('ANIMATION_MODEL_OWNERSHIP_UNKNOWN');
 }
 async function enterI2V(){
  assertKnown();
  if(busy)throw error('ANIMATION_MODEL_SLOT_BUSY');
  busy=true;
  try{
   // The healthy legacy channel confirms the owned engine group is gone, then
   // waits for the helper itself to exit. TTL is deliberately not used here.
   if(await wan.shutdown()!==true)throw error('ANIMATION_WAN_RELEASE_UNCONFIRMED');
  }catch(e){
   unknown=true;busy=false;throw e;
  }
 }
 return {
  assertKnown,
  async runI2V(job){
   await enterI2V();
   try{
    // A successful return guarantees owned I2V process group already exited.
    // Confirmed failures explicitly carry the same proof.
    return await i2v.render(job);
   }catch(e){
    if(e?.rendererJoined!==true)unknown=true;
    throw e;
   }finally{busy=false;}
  },
  enterWan(){
   assertKnown();
   if(busy)throw error('ANIMATION_MODEL_SLOT_BUSY');
  },
 };
}
