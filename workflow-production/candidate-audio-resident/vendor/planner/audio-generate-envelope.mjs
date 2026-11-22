/** Initial one-result audio rollout admits BGM only. */
export const audioRequestType='audio.generate';
export const audioSchema='ngs.audio-generate.v1';
export const audioTaskKey='audio_generate';
export const audioKind='bgm';
export const audioKinds=Object.freeze([audioKind]);
export const audioTitleLimit=120,audioBriefLimit=4000,audioVocalTextLimit=4000;
export const audioConceptIdPattern='^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$';
const conceptId=new RegExp(audioConceptIdPattern);
export const audioDuration=Object.freeze({min:10,max:30,autoMax:120});
export const audioDurationTarget=60;
const fields=['brief','conceptId','durationSeconds','kind','projectId','schemaVersion','text','title'];
const control=/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f]/u;
const string=(v,max,multiline)=>typeof v==='string'&&v.isWellFormed()&&v===v.trim()&&v.length>0&&
 [...v].length<=max&&!control.test(v)&&(multiline||!/[\r\n\t\u2028\u2029]/u.test(v));

export function validateAudioGenerate(value,projectId){
 const bad=()=>Error('NGS_AUDIO_GENERATE_PAYLOAD_INVALID');
 const hasTarget=!!value&&Object.hasOwn(value,'targetDurationSeconds');
 const keys=hasTarget?fields.concat('targetDurationSeconds').sort():fields;
 if(!value||typeof value!=='object'||Array.isArray(value)||Object.keys(value).length!==keys.length||
  !keys.every(k=>Object.hasOwn(value,k))||hasTarget&&(value.durationSeconds!==null||value.targetDurationSeconds!==audioDurationTarget)||value.schemaVersion!==audioSchema||projectId!=='binari'||
  value.projectId!==projectId||value.kind!==audioKind||
  !(value.durationSeconds===null
   ? value.text===''||string(value.text,audioVocalTextLimit,true)
   : value.text===''&&Number.isFinite(value.durationSeconds)&&value.durationSeconds>=audioDuration.min&&value.durationSeconds<=audioDuration.max)||
  !string(value.title,audioTitleLimit,false)||!string(value.brief,audioBriefLimit,true)||
  !string(value.conceptId,128,false)||!conceptId.test(value.conceptId))throw bad();
 return JSON.stringify(Object.fromEntries(keys.map(k=>[k,value[k]])));
}
