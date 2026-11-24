// Native Wan2.2 5B TI2V graph adapted from the official Comfy-Org template:
// https://raw.githubusercontent.com/Comfy-Org/workflow_templates/refs/heads/main/templates/video_wan2_2_5B_ti2v.json
// Diffs: pinned Q8_0 GGUF backbone, existing local UMT5 encoder, owned source,
// bounded canvas/length and direct RGB PNG output in place of MP4.
export const WAN_I2V_MODEL = 'Wan2.2-TI2V-5B-Q8_0.gguf';
export const WAN_I2V_MODEL_SHA256 = '57bece983817ab2f957546683bb670f13be7d99022d45674840cd999a050ea8f';
export const WAN_I2V_ENCODER = 'umt5_xxl_fp16.safetensors';
export const WAN_I2V_VAE = 'wan2.2_vae.safetensors';
export const WAN_I2V_INPUT_IMAGE = 'source.png';
export const WAN_I2V_SAVE_NODE = '10';
export const WAN_I2V_SAVE_PREFIX = 'i2v_rgb';
export const WAN_I2V_FPS = 24;
export const WAN_I2V_LENGTH = 45; // 4k+1 for Wan22; output playing time 45/24s.
const NEGATIVE = 'flicker, abrupt cuts, extra limbs, duplicate characters, text, watermark';

export function buildWanI2VGraph({width,height,motionPrompt,seed=417}={}) {
  const dimension = v => Number.isInteger(v) && v>=128 && v<=512 && v%32===0;
  if (!dimension(width) || !dimension(height) || !Number.isSafeInteger(seed) || seed<0 ||
      typeof motionPrompt!=='string' || !motionPrompt.trim() || Buffer.byteLength(motionPrompt)>4096 ||
      // Comfy CLIP treats embedding: references as disk-backed assets, not inert text.
      /\bembedding:|[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/iu.test(motionPrompt))
    throw Object.assign(new Error('I2V_WAN_GRAPH_INPUT_INVALID'),{code:'I2V_WAN_GRAPH_INPUT_INVALID'});
  return {
    '1': {class_type:'UnetLoaderGGUF',inputs:{unet_name:WAN_I2V_MODEL}},
    '2': {class_type:'CLIPLoader',inputs:{clip_name:WAN_I2V_ENCODER,type:'wan',device:'default'}},
    '3': {class_type:'VAELoader',inputs:{vae_name:WAN_I2V_VAE}},
    '4': {class_type:'LoadImage',inputs:{image:WAN_I2V_INPUT_IMAGE}},
    '5': {class_type:'CLIPTextEncode',inputs:{clip:['2',0],text:motionPrompt.trim()}},
    '6': {class_type:'CLIPTextEncode',inputs:{clip:['2',0],text:NEGATIVE}},
    '7': {class_type:'ModelSamplingSD3',inputs:{model:['1',0],shift:8}},
    '8': {class_type:'Wan22ImageToVideoLatent',inputs:{vae:['3',0],start_image:['4',0],width,height,length:WAN_I2V_LENGTH,batch_size:1}},
    '9': {class_type:'KSampler',inputs:{model:['7',0],positive:['5',0],negative:['6',0],latent_image:['8',0],
      seed,steps:20,cfg:5,sampler_name:'uni_pc',scheduler:'simple',denoise:1}},
    '11': {class_type:'VAEDecode',inputs:{samples:['9',0],vae:['3',0]}},
    [WAN_I2V_SAVE_NODE]: {class_type:'SaveImage',inputs:{images:['11',0],filename_prefix:WAN_I2V_SAVE_PREFIX}},
  };
}
