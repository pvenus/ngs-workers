/** One physical deletion target, pinned to the exact published revision selected. */
export const fileDeleteType='file.delete',fileDeleteSchema='ngs.file-delete.v1',fileDeleteTaskKey='file_delete';
export const propDeleteRoot='canonical-files/binari/props';
export const npcDeleteRoot='canonical-files/binari/npcs';
export const assemblyDeleteRoot='canonical-files/binari/cinematics/assemblies';
export const mapDeleteRoot='canonical-files/binari/cinematics/maps';
export const backgroundDeleteRoot='canonical-files/binari/cinematics/backgrounds';
const background=/^[a-f0-9]{64}\.(?:png|json)$/u;
const source=/^request\.official\.[a-f0-9]{64}$/u;
export const cinematicDeleteRoot='canonical-files/binari/cinematics';
const sourcePath=/^sources\/([a-f0-9]{64})\.(png|json)$/u;
const sequencePath=/^sequences\/request\.official\.[a-f0-9]{64}$/u;
const resultPath=/^results\/request\.official\.[a-f0-9]{64}\.(mp4|json)$/u;
export function cinematicDeleteTarget(path,kind,sourceSha256){
 if(typeof path!=='string'||path.trim()!==path)return false;
 if(kind==='directory')return sequencePath.test(path)||path==='scenes/'+sourceSha256;
 if(kind!=='file')return false;
 const source=sourcePath.exec(path);
 return source?source[1]===sourceSha256:resultPath.test(path);
}
const npc=/^(?:character|npc|entity|enemy)\.[a-z0-9._-]{2,80}$/u;
const task=/^workflow\.task\.[a-f0-9]{64}$/u;
const prop=/^[a-z0-9]+(?:[-._][a-z0-9]+)*$/u;
const sha=/^[a-f0-9]{64}$/u;
const exact=(v,k)=>v&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).length===k.length&&k.every(x=>Object.hasOwn(v,x));
export function validateFileDelete(value,projectId){
 const t=value?.target,p=value?.expected;
 const common=exact(value,['schemaVersion','projectId','target','expected'])&&value.schemaVersion===fileDeleteSchema&&
  projectId==='binari'&&value.projectId===projectId&&exact(t,['root','path','kind'])&&
  typeof t.path==='string'&&t.path.trim()===t.path;
 let expected;
 if(common&&t.kind==='directory'&&t.root===propDeleteRoot&&t.path.length>=2&&t.path.length<=80&&prop.test(t.path)&&
    exact(p,['revision','sha256'])&&Number.isSafeInteger(p.revision)&&p.revision>=1&&p.revision<=2147483647&&
    typeof p.sha256==='string'&&p.sha256.length===64&&sha.test(p.sha256))
  expected={revision:p.revision,sha256:p.sha256};
 if(common&&t.kind==='directory'&&t.root===npcDeleteRoot&&npc.test(t.path)&&exact(p,['creationTaskId'])&&
    typeof p.creationTaskId==='string'&&p.creationTaskId.length===78&&task.test(p.creationTaskId))
  expected={creationTaskId:p.creationTaskId};
 if(common&&t.kind==='file'&&t.root===backgroundDeleteRoot&&background.test(t.path)&&exact(p,['sourceRequestId'])&&
    typeof p.sourceRequestId==='string'&&p.sourceRequestId.length===81&&source.test(p.sourceRequestId))expected={sourceRequestId:p.sourceRequestId};
 if(common&&t.root===cinematicDeleteRoot&&exact(p,['sourceSha256'])&&typeof p.sourceSha256==='string'&&
    p.sourceSha256.length===64&&sha.test(p.sourceSha256)&&cinematicDeleteTarget(t.path,t.kind,p.sourceSha256))
  expected={sourceSha256:p.sourceSha256};
 if(common&&t.kind==='directory'&&[mapDeleteRoot,assemblyDeleteRoot].includes(t.root)&&source.test(t.path)&&
    exact(p,['revision','officialRequestId'])&&Number.isSafeInteger(p.revision)&&p.revision>=1&&
    typeof p.officialRequestId==='string'&&source.test(p.officialRequestId))
  expected={officialRequestId:p.officialRequestId,revision:p.revision};
 if(!expected)throw Error('NGS_FILE_DELETE_PAYLOAD_INVALID');
 return JSON.stringify({expected,projectId,schemaVersion:fileDeleteSchema,target:{kind:t.kind,path:t.path,root:t.root}});
}
