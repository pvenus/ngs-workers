import assert from 'node:assert/strict';
import {test} from 'node:test';
import {
  buildWanI2VGraph,WAN_I2V_MODEL,WAN_I2V_MODEL_SHA256,WAN_I2V_ENCODER,WAN_I2V_VAE,WAN_I2V_INPUT_IMAGE,
  WAN_I2V_SAVE_NODE,WAN_I2V_SAVE_PREFIX,WAN_I2V_FPS,WAN_I2V_LENGTH,
} from './i2v-wan-graph.mjs';
const valid={width:320,height:512,motionPrompt:'  손을 천천히 든다.  ',seed:417};
test('pinned native graph connects the exact input image through one sampler to the sole frame sink',()=>{
  const g=buildWanI2VGraph(valid);
  assert.deepEqual(g['1'],{class_type:'UnetLoaderGGUF',inputs:{unet_name:WAN_I2V_MODEL}});
  assert.match(WAN_I2V_MODEL,/Q8_0\.gguf$/);
  assert.match(WAN_I2V_MODEL_SHA256,/^[a-f0-9]{64}$/);
  assert.deepEqual(g['2'].inputs,{clip_name:WAN_I2V_ENCODER,type:'wan',device:'default'});
  assert.equal(g['3'].inputs.vae_name,WAN_I2V_VAE);
  assert.equal(g['4'].inputs.image,WAN_I2V_INPUT_IMAGE);
  assert.deepEqual(g['7'].inputs,{model:['1',0],shift:8});
  assert.deepEqual(g['8'].inputs,{vae:['3',0],start_image:['4',0],width:320,height:512,length:45,batch_size:1});
  assert.deepEqual(g['9'].inputs,{model:['7',0],positive:['5',0],negative:['6',0],latent_image:['8',0],
    seed:417,steps:20,cfg:5,sampler_name:'uni_pc',scheduler:'simple',denoise:1});
  assert.equal(g['5'].inputs.text,'손을 천천히 든다.');
  assert.deepEqual(g['11'].inputs,{samples:['9',0],vae:['3',0]});
  assert.deepEqual(Object.values(g).filter(n=>/^Save/.test(n.class_type)),[g[WAN_I2V_SAVE_NODE]]);
  assert.deepEqual(g[WAN_I2V_SAVE_NODE].inputs,{images:['11',0],filename_prefix:WAN_I2V_SAVE_PREFIX});
  assert.equal((WAN_I2V_LENGTH-1)%4,0);
  assert.equal(WAN_I2V_FPS,24);
});
test('invalid batch/prompt inputs cannot escape the pinned graph',()=>{
  for(const bad of [{width:127},{height:513},{height:249},{width:'320'},{seed:-1},{seed:2**54},{motionPrompt:' \n '},
    {motionPrompt:'embedding:relative'}, {motionPrompt:'(EMBEDDING:path:0.5)'},{motionPrompt:'bad\0text'},{motionPrompt:'é'.repeat(2050)}])
    assert.throws(()=>buildWanI2VGraph({...valid,...bad}),{code:'I2V_WAN_GRAPH_INPUT_INVALID'});
  const g=buildWanI2VGraph({...valid,image:'anything.png',model:'other.bin',length:500,batch_size:100});
  assert.equal(g['4'].inputs.image,WAN_I2V_INPUT_IMAGE);
  assert.equal(g['1'].inputs.unet_name,WAN_I2V_MODEL);
  assert.equal(g['8'].inputs.length,WAN_I2V_LENGTH);
  assert.equal(g['8'].inputs.batch_size,1);
});
