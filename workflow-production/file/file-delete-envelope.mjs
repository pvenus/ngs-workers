/** One physical deletion target, pinned to the exact published revision selected. */
export const fileDeleteType='file.delete',fileDeleteSchema='ngs.file-delete.v1',fileDeleteTaskKey='file_delete';
export const propDeleteRoot='canonical-files/binari/props';
const prop=/^[a-z0-9]+(?:[-._][a-z0-9]+)*$/u;
const sha=/^[a-f0-9]{64}$/u;
const exact=(v,k)=>v&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).length===k.length&&k.every(x=>Object.hasOwn(v,x));
export function validateFileDelete(value,projectId){
 const t=value?.target,p=value?.expected;
 if(!exact(value,['schemaVersion','projectId','target','expected'])||value.schemaVersion!==fileDeleteSchema||
    projectId!=='binari'||value.projectId!==projectId||!exact(t,['root','path','kind'])||
    t.root!==propDeleteRoot||t.kind!=='directory'||typeof t.path!=='string'||t.path.length<2||t.path.length>80||t.path.trim()!==t.path||!prop.test(t.path)||
    !exact(p,['revision','sha256'])||!Number.isSafeInteger(p.revision)||p.revision<1||p.revision>2147483647||
    typeof p.sha256!=='string'||p.sha256.length!==64||!sha.test(p.sha256))
  throw Error('NGS_FILE_DELETE_PAYLOAD_INVALID');
 return JSON.stringify({expected:{revision:p.revision,sha256:p.sha256},projectId,schemaVersion:fileDeleteSchema,target:{kind:t.kind,path:t.path,root:t.root}});
}
