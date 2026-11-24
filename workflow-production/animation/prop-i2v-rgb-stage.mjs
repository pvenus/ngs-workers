/** Pure adapter from verified RGB I2V output to the formal prop PDS stage. */
import {createHash} from 'node:crypto';
import {outputArtifact} from './i2v-worker.mjs';

const sha=v=>createHash('sha256').update(v).digest('hex');
const canonical=v=>Array.isArray(v)?'['+v.map(canonical).join(',')+']':v&&typeof v==='object'?
 '{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+canonical(v[k])).join(',')+'}':JSON.stringify(v);
const fail=()=>{throw Error('PROP_I2V_RGB_STAGE_INVALID');};
export const propStagePath='/v1/props/ownership/workflow-stage';
export const propStageCapability='workflow.prop-stage.v1';

export function propI2VRGBStage(output,{officialRequestId,taskId,propId,sourceSha256}={}){
 if(typeof officialRequestId!=='string'||officialRequestId.length!==81||
    !/^request\.official\.[a-f0-9]{64}$/u.test(officialRequestId)||
    taskId!=='workflow.task.'+sha(canonical([officialRequestId,'prop_motion']))||
    typeof propId!=='string'||propId.length<2||propId.length>80||propId.trim()!==propId||
    !/^[a-z0-9]+(?:[-._][a-z0-9]+)*$/u.test(propId)||
    typeof sourceSha256!=='string'||sourceSha256.length!==64||!/^[a-f0-9]{64}$/u.test(sourceSha256))fail();
 const checked=outputArtifact(output,sourceSha256),{fps,width,height,frames}=checked;
 if(width*height*frames.length>30_000_000)fail();
 const frameDurationsMs=frames.map((_,i)=>Math.round((i+1)*1000/fps)-Math.round(i*1000/fps));
 const duration=frameDurationsMs.reduce((n,v)=>n+v,0);
 if(duration<100||duration>10_000||frameDurationsMs.some(v=>v<10||v>1000))fail();
 const manifest={schemaVersion:'ngs.prop-native-rgb-sequence.v1',officialRequestId,taskId,propId,
  width,height,frameDurationsMs,frames:output.frames.map((png,index)=>({index,sha256:sha(png),
   bytes:png.length,mediaType:'image/png'}))};
 return {artifact:{manifest,frames},resultRef:'prop.i2v-rgb.'+sha(canonical(manifest))};
}
