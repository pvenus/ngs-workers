import assert from 'node:assert/strict';
import {test} from 'node:test';
import {
 buildWanI2VGraph,WAN_I2V_HIGH,WAN_I2V_LOW,WAN_I2V_HIGH_SHA256,WAN_I2V_LOW_SHA256,WAN_I2V_ENCODER,
 WAN_I2V_VAE,WAN_I2V_VAE_SHA256,WAN_I2V_INPUT_IMAGE,WAN_I2V_SAVE_NODE,WAN_I2V_SAVE_PREFIX,WAN_I2V_FPS,WAN_I2V_LENGTH,
} from './i2v-wan-graph.mjs';
const valid={width:320,height:512,motionPrompt:'  손을 천천히 든다.  ',seed:417};
test('A14B-only graph uses exact high/low expert pair and the official non-turbo native sampler handoff',()=>{
 const g=buildWanI2VGraph(valid);
 assert.deepEqual(Object.values(g).filter(n=>n.class_type==='UnetLoaderGGUF').map(n=>n.inputs.unet_name),[WAN_I2V_HIGH,WAN_I2V_LOW]);
 assert.match(WAN_I2V_HIGH,/HighNoise-Q4_K_M\.gguf$/);
 assert.match(WAN_I2V_LOW,/LowNoise-Q4_K_M\.gguf$/);
 assert.equal(WAN_I2V_HIGH_SHA256,'836250abfaa3411694e2c9cf3a0cc18265329d5156d81aa116d5366b0f8f02e7');
 assert.equal(WAN_I2V_LOW_SHA256,'e2f98d834af009d035c6b0918268f2eba0aa8a63025ce942277e2384d40b0866');
 assert.deepEqual(g['2'].inputs,{clip_name:WAN_I2V_ENCODER,type:'wan',device:'default'});
 assert.equal(g['3'].inputs.vae_name,'wan_2.1_vae.safetensors');
 assert.equal(WAN_I2V_VAE_SHA256,'2fc39d31359a4b0a64f55876d8ff7fa8d780956ae2cb13463b0223e15148976b');
 assert.equal(g['4'].inputs.image,WAN_I2V_INPUT_IMAGE);
 assert.deepEqual(g['7'].inputs,{model:['1',0],shift:5});
 assert.deepEqual(g['13'].inputs,{model:['12',0],shift:5});
 assert.deepEqual(g['8'],{class_type:'WanImageToVideo',inputs:{positive:['5',0],negative:['6',0],vae:['3',0],
  start_image:['4',0],width:320,height:512,length:45,batch_size:1}});
 const expectedCommon={positive:['8',0],negative:['8',1],steps:20,cfg:3.5,sampler_name:'euler',scheduler:'simple'};
 assert.deepEqual(g['9'],{class_type:'KSamplerAdvanced',inputs:{...expectedCommon,model:['7',0],latent_image:['8',2],
  noise_seed:417,add_noise:'enable',start_at_step:0,end_at_step:10,return_with_leftover_noise:'enable'}});
 assert.deepEqual(g['14'],{class_type:'KSamplerAdvanced',inputs:{...expectedCommon,model:['13',0],latent_image:['9',0],
  noise_seed:0,add_noise:'disable',start_at_step:10,end_at_step:20,return_with_leftover_noise:'disable'}});
 assert.equal(g['5'].inputs.text,'손을 천천히 든다.');
 assert.deepEqual(g['11'].inputs,{samples:['14',0],vae:['3',0]});
 assert.deepEqual(Object.values(g).filter(n=>/^Save/.test(n.class_type)),[g[WAN_I2V_SAVE_NODE]]);
 assert.deepEqual(g[WAN_I2V_SAVE_NODE].inputs,{images:['11',0],filename_prefix:WAN_I2V_SAVE_PREFIX});
 assert.ok(!JSON.stringify(g).match(/Q8_0|5B|LoRA|Wan22ImageToVideoLatent/));
 assert.equal((WAN_I2V_LENGTH-1)%4,0);assert.equal(WAN_I2V_FPS,24);
});
test('invalid batch/prompt inputs cannot escape the pinned graph',()=>{
 for(const bad of [{width:127},{height:513},{height:249},{width:'320'},{seed:-1},{seed:2**54},{motionPrompt:' \n '},
  {motionPrompt:'embedding:relative'},{motionPrompt:'(EMBEDDING:path:0.5)'},{motionPrompt:'bad\0text'},
  {motionPrompt:'\udfff'},{motionPrompt:'é'.repeat(2050)}])
  assert.throws(()=>buildWanI2VGraph({...valid,...bad}),{code:'I2V_WAN_GRAPH_INPUT_INVALID'});
 const g=buildWanI2VGraph({...valid,image:'anything.png',model:'other.bin',lowModel:'invalid',vae:'other',
  length:500,batch_size:100,steps:0});
 assert.equal(g['4'].inputs.image,WAN_I2V_INPUT_IMAGE);
 assert.equal(g['1'].inputs.unet_name,WAN_I2V_HIGH);
 assert.equal(g['12'].inputs.unet_name,WAN_I2V_LOW);
 assert.equal(g['3'].inputs.vae_name,WAN_I2V_VAE);
 assert.equal(g['8'].inputs.length,WAN_I2V_LENGTH);
 assert.equal(g['8'].inputs.batch_size,1);
 assert.equal(g['9'].inputs.steps,20);
});
