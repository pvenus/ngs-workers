import {constants} from 'node:fs';
import {open,realpath} from 'node:fs/promises';
import {isAbsolute,resolve} from 'node:path';
import {createAudioAdapters} from './provider/audio-adapters.mjs';

export async function loadAudioAdapters(configPath){
 const fail=()=>{throw Error('AUDIO_ADAPTER_BINDING_INVALID');};
 if(typeof configPath!=='string'||!isAbsolute(configPath)||resolve(configPath)!==configPath||await realpath(configPath)!==configPath)fail();
 const fd=await open(configPath,constants.O_RDONLY|constants.O_NOFOLLOW);
 let conf;
 try{
  const st=await fd.stat();
  if(!st.isFile()||st.uid!==process.getuid()||st.nlink!==1||(st.mode&0o777)!==0o600||st.size>4096)fail();
  conf=JSON.parse(await fd.readFile({encoding:'utf8'}));
 }finally{await fd.close();}
 if(!conf||typeof conf!=='object'||Object.keys(conf).sort().join(',')!=='hqBinding,schemaVersion'||
  conf.schemaVersion!=='workflow.bgm-adapter-binding.v1')fail();
 return createAudioAdapters({hqBinding:conf.hqBinding});
}
