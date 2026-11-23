/** Exact intent for producing derivative stems from an existing published WAV. */
export const audioSeparateType='audio.separate';
export const audioSeparateSchema='ngs.audio-separate.v1';
export const audioSeparateTaskKey='audio_separate';
export const audioSeparateSourceIdPattern='^request\\.official\\.[a-f0-9]{64}$';
export const audioSeparateShaPattern='^[a-f0-9]{64}$';
const sourceId=new RegExp(audioSeparateSourceIdPattern),sha=new RegExp(audioSeparateShaPattern);
const keys=['projectId','schemaVersion','sourceOfficialRequestId','sourceSha256'];
export function validateAudioSeparate(value,projectId){
 if(!value||typeof value!=='object'||Array.isArray(value)||Object.keys(value).length!==keys.length||
    !keys.every(k=>Object.hasOwn(value,k))||projectId!=='binari'||value.projectId!==projectId||
    value.schemaVersion!==audioSeparateSchema||
    typeof value.sourceOfficialRequestId!=='string'||value.sourceOfficialRequestId.length!==81||!sourceId.test(value.sourceOfficialRequestId)||
    typeof value.sourceSha256!=='string'||value.sourceSha256.length!==64||!sha.test(value.sourceSha256))
  throw Error('NGS_AUDIO_SEPARATE_PAYLOAD_INVALID');
 return JSON.stringify(Object.fromEntries(keys.map(k=>[k,value[k]])));
}
