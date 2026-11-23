/** Retire exactly one previously published official BGM. */
export const audioDeleteType='audio.delete',audioDeleteSchema='ngs.audio-delete.v1',audioDeleteTaskKey='audio_delete';
export const audioOfficialIdPattern='^request\\.official\\.[a-f0-9]{64}$';
const officialId=new RegExp(audioOfficialIdPattern);
const keys=['officialRequestId','projectId','schemaVersion'];
export function validateAudioDelete(value,projectId){
 if(!value||typeof value!=='object'||Array.isArray(value)||Object.keys(value).length!==keys.length||
    !keys.every(k=>Object.hasOwn(value,k))||projectId!=='binari'||value.projectId!==projectId||
    value.schemaVersion!==audioDeleteSchema||typeof value.officialRequestId!=='string'||
    value.officialRequestId.length!==81||!officialId.test(value.officialRequestId))throw Error('NGS_AUDIO_DELETE_PAYLOAD_INVALID');
 return JSON.stringify(Object.fromEntries(keys.map(k=>[k,value[k]])));
}
