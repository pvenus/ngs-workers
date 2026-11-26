// Native Wan2.2 I2V-A14B, from Comfy-Org's non-turbo 14B I2V template:
// https://github.com/Comfy-Org/workflow_templates/blob/main/templates/video_wan2_2_14B_i2v.json
// Fixed GGUF experts replace the matching native loaders. No LoRA or fallback.
export const WAN_I2V_MODEL_ID='wan2.2-i2v-a14b';
export const WAN_I2V_QUANTIZATION='q4_k_m';
export const WAN_I2V_PROVIDER='wan-i2v-a14b-q4_k_m';
export const WAN_I2V_HIGH='Wan2.2-I2V-A14B-HighNoise-Q4_K_M.gguf';
export const WAN_I2V_LOW='Wan2.2-I2V-A14B-LowNoise-Q4_K_M.gguf';
export const WAN_I2V_HIGH_SHA256='836250abfaa3411694e2c9cf3a0cc18265329d5156d81aa116d5366b0f8f02e7';
export const WAN_I2V_LOW_SHA256='e2f98d834af009d035c6b0918268f2eba0aa8a63025ce942277e2384d40b0866';
export const WAN_I2V_ENCODER='umt5_xxl_fp16.safetensors';
export const WAN_I2V_VAE='wan_2.1_vae.safetensors';
export const WAN_I2V_VAE_SHA256='2fc39d31359a4b0a64f55876d8ff7fa8d780956ae2cb13463b0223e15148976b';

export const WAN_I2V_INPUT_IMAGE='source.png';
export const WAN_I2V_SAVE_NODE='10';
export const WAN_I2V_SAVE_PREFIX='i2v_rgb';
export const WAN_I2V_FPS=24; // Existing NGS artifact playback, not a model input.
export const WAN_I2V_SHORT_LENGTH=17;
export const WAN_I2V_LENGTH=45; // 4k+1.
const NEGATIVE='flicker, abrupt cuts, extra limbs, duplicate characters, text, watermark';
const STEPS=20,SPLIT=10,CFG=3.5,SHIFT=5;

export function buildWanI2VGraph({width,height,motionPrompt,seed=417,length=WAN_I2V_LENGTH}={}){
 const dimension=v=>Number.isInteger(v)&&v>=128&&v<=512&&v%32===0;
 if(![WAN_I2V_LENGTH,WAN_I2V_SHORT_LENGTH].includes(length)||!dimension(width)||!dimension(height)||!Number.isSafeInteger(seed)||seed<0||
    typeof motionPrompt!=='string'||!motionPrompt.isWellFormed()||!motionPrompt.trim()||Buffer.byteLength(motionPrompt)>4096||
    /\bembedding:|[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]/iu.test(motionPrompt))
  throw Object.assign(Error('I2V_WAN_GRAPH_INPUT_INVALID'),{code:'I2V_WAN_GRAPH_INPUT_INVALID'});
 const common={positive:['8',0],negative:['8',1],steps:STEPS,cfg:CFG,sampler_name:'euler',scheduler:'simple'};
 return {
  '1':{class_type:'UnetLoaderGGUF',inputs:{unet_name:WAN_I2V_HIGH}},
  '2':{class_type:'CLIPLoader',inputs:{clip_name:WAN_I2V_ENCODER,type:'wan',device:'default'}},
  '3':{class_type:'VAELoader',inputs:{vae_name:WAN_I2V_VAE}},
  '4':{class_type:'LoadImage',inputs:{image:WAN_I2V_INPUT_IMAGE}},
  '5':{class_type:'CLIPTextEncode',inputs:{clip:['2',0],text:motionPrompt.trim()}},
  '6':{class_type:'CLIPTextEncode',inputs:{clip:['2',0],text:NEGATIVE}},
  '7':{class_type:'ModelSamplingSD3',inputs:{model:['1',0],shift:SHIFT}},
  '8':{class_type:'WanImageToVideo',inputs:{positive:['5',0],negative:['6',0],vae:['3',0],start_image:['4',0],
   width,height,length,batch_size:1}},
  '9':{class_type:'KSamplerAdvanced',inputs:{...common,model:['7',0],latent_image:['8',2],noise_seed:seed,
   add_noise:'enable',start_at_step:0,end_at_step:SPLIT,return_with_leftover_noise:'enable'}},
  '11':{class_type:'VAEDecode',inputs:{samples:['14',0],vae:['3',0]}},
  '12':{class_type:'UnetLoaderGGUF',inputs:{unet_name:WAN_I2V_LOW}},
  '13':{class_type:'ModelSamplingSD3',inputs:{model:['12',0],shift:SHIFT}},
  '14':{class_type:'KSamplerAdvanced',inputs:{...common,model:['13',0],latent_image:['9',0],noise_seed:0,
   add_noise:'disable',start_at_step:SPLIT,end_at_step:STEPS,return_with_leftover_noise:'disable'}},
  [WAN_I2V_SAVE_NODE]:{class_type:'SaveImage',inputs:{images:['11',0],filename_prefix:WAN_I2V_SAVE_PREFIX}},
 };
}
