import {mapFields, mapCanonical, officialId, validMapGraph} from './map-contract.mjs';

export const fileUpdateType='file.update',fileUpdateSchema='ngs.file-update.v1';
export const fileUpdateTaskKey='file_update',fileUpdatePds='/v1/storage/workflow-file-update';
export const mapUpdateRoot='canonical-files/binari/cinematics/maps';
const pin=p=>mapFields(p,['revision','officialRequestId'])&&Number.isSafeInteger(p.revision)&&
 p.revision>=1&&p.revision<Number.MAX_SAFE_INTEGER&&officialId(p.officialRequestId);

// Adapters own content validation. Never accept arbitrary filesystem paths or partial patches.
const targets=Object.freeze({
 [mapUpdateRoot]:{
  id:officialId, content:validMapGraph,
  ref:(id,revision)=>'binari/cinematics/maps/'+id+'/'+revision+'.json',
 },
});
export function validFileUpdate(p){
 const t=p?.target,a=Object.hasOwn(targets,t?.root)?targets[t.root]:null;
 return mapFields(p,['schemaVersion','projectId','target','expected','content'])&&
  p.schemaVersion===fileUpdateSchema&&p.projectId==='binari'&&
  mapFields(t,['root','path','kind'])&&t.kind==='metadata'&&a?.id(t.path)===true&&pin(p.expected)&&a.content(p.content)&&
  new TextEncoder().encode(mapCanonical(p)).length<=60*1024;
}
export function validateFileUpdate(p){
 if(!validFileUpdate(p))throw Error('NGS_FILE_UPDATE_PAYLOAD_INVALID');
 return mapCanonical(p);
}
export const fileUpdateRef=p=>targets[p.target.root].ref(p.target.path,p.expected.revision+1);
