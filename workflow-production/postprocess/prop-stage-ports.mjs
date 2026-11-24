/** Loopback-only formal prop media and publication transport. */
import {fixedMotionHttp} from './preset-video-ports.mjs';
import {exactProp} from './vendor/prop-composite/prop-generate-envelope.mjs';
import {propPackageMediaLimit} from './vendor/prop-composite/prop-package-contract.mjs';

export const stagePath='/v1/props/ownership/workflow-stage';
export const packagePath='/v1/props/ownership/workflow-package';
const decoder=new TextDecoder('utf-8',{fatal:true});
export function propStagePorts({transport=fixedMotionHttp}={}){
 const json=async(path,{body,signal,timeout=5000,limit=1024}={})=>
  JSON.parse(decoder.decode(await transport(path,{body,signal,timeout,limit,type:'application/json'})));
 const capabilities=async(signal)=>{
   try{
    const [stage,pack]=await Promise.all([json(stagePath,{signal}),json(packagePath,{signal})]);
    if(!(exactProp(stage,['enabled','schemaVersion'])&&stage.enabled===true&&stage.schemaVersion==='workflow.prop-stage.v1'&&
      exactProp(pack,['enabled','schemaVersion','modes'])&&pack.enabled===true&&pack.schemaVersion==='workflow.prop-package-write.v1'&&
      Array.isArray(pack.modes)&&pack.modes[0]==='still'&&(pack.modes.length===1||
       pack.modes.length===2&&pack.modes[1]==='append-animation')))return null;
    return {still:true,appendEnabled:pack.modes.length===2};
   }catch{return null;}
 };
 return {
  capabilities,
  ready:async signal=>Boolean(await capabilities(signal)),
  readPropDerivation({claim},{signal}={}){
   return json(stagePath+'/read',{body:{claim},signal,limit:64*1024,timeout:120000});
  },
  fetchPropDerivationAsset({claim},descriptor,{signal,purpose='input'}={}){
   return transport(stagePath+'/asset',{body:{claim,purpose,index:descriptor.index??null},signal,
    type:descriptor.mediaType,limit:propPackageMediaLimit,timeout:120000});
  },
  stagePropDerivation({claim},{media,bytes}){
   // After dispatch, drain this bounded write independent of shutdown; a
   // missing ACK cannot establish that the stage did not durably commit.
   return json(stagePath,{body:{claim,artifact:{media,base64:bytes.toString('base64')}},timeout:180000});
  },
  preparePropPublication(claim,{signal}={}){
   return json(packagePath+'/prepare',{body:{claim},signal,limit:512*1024+8192,timeout:120000});
  },
  publishPropPackage(claim,manifest){
   return json(packagePath,{body:{claim,manifest},timeout:180000});
  },
 };
}
