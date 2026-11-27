import {mapCanonical,mapFields,officialId} from './map-contract.mjs';
export const assemblyTypes=['assembly.generate','assembly.update'];
export const assemblySchemas=['ngs.assembly-generate.v1','ngs.assembly-update.v1'];
export const assemblyKind='binari.ritual-well-water-demo',assemblyStage='assembly_metadata';
export const assemblyPds='/v1/assemblies/ownership/workflow-assembly';
export const assemblyRef=(id,rev)=>'binari/cinematics/assemblies/'+id+'/'+rev+'.json';
const int=(v,lo,hi)=>Number.isSafeInteger(v)&&lo<=v&&v<=hi;
const ident=v=>typeof v==='string'&&/^[a-z0-9]+(?:[-._][a-z0-9]+)*$/u.test(v)&&v.length>=2&&v.length<=80;
const region=v=>typeof v==='string'&&/^[a-z][a-z0-9-]{0,31}$/u.test(v);
const text=(v,max)=>typeof v==='string'&&v.isWellFormed()&&v===v.trim()&&v.length>=1&&v.length<=max&&!/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/u.test(v);
const xy=(v,lo,hi)=>int(v.x,lo,hi)&&int(v.y,lo,hi);
const station=v=>mapFields(v,['regionId','x','y'])&&region(v.regionId)&&xy(v,10,90);
const offset=v=>mapFields(v,['x','y'])&&xy(v,-12,12);
const pin=v=>mapFields(v,['propId','revision','sha256'])&&ident(v.propId)&&int(v.revision,1,Number.MAX_SAFE_INTEGER-1)&&
 typeof v.sha256==='string'&&/^[a-f0-9]{64}$/u.test(v.sha256);
const roles=['table','bowl','well','lid'];
export const validPuzzles=v=>Array.isArray(v)&&v.length<=32&&new Set(v.map(x=>x?.id)).size===v.length&&v.every(p=>
 mapFields(p,['id','type','layout','attachments','bindings'])&&ident(p.id)&&p.type===assemblyKind&&
 mapFields(p.layout,['altar','well'])&&station(p.layout.altar)&&station(p.layout.well)&&
 mapFields(p.attachments,['bowl','lid'])&&offset(p.attachments.bowl)&&offset(p.attachments.lid)&&
 mapFields(p.bindings,roles)&&roles.every(k=>pin(p.bindings[k]))&&new Set(roles.map(k=>p.bindings[k].propId)).size===4);
export function validateAssembly(v,type){
 const i=assemblyTypes.indexOf(type);
 const fields=i===0?['title','mapId','puzzleType','intent']:['assemblyId','expectedRevision','puzzles'];
 if(i<0||!mapFields(v,['schemaVersion','projectId',...fields])||v.schemaVersion!==assemblySchemas[i]||v.projectId!=='binari'||
  !(i===0?text(v.title,100)&&officialId(v.mapId)&&v.puzzleType===assemblyKind&&text(v.intent,2000):
    officialId(v.assemblyId)&&int(v.expectedRevision,1,Number.MAX_SAFE_INTEGER-2)&&validPuzzles(v.puzzles)))throw Error('NGS_ASSEMBLY_PAYLOAD_INVALID');
 return mapCanonical(v);
}
const obj=properties=>({type:'object',additionalProperties:false,required:Object.keys(properties),properties});
const string=(minLength,maxLength,pattern)=>({type:'string',minLength,maxLength,...(pattern?{pattern}:{})});
const integer=(minimum,maximum)=>({type:'integer',minimum,maximum});
const oid=string(81,81,'^request\\.official\\.[a-f0-9]{64}$');
const slug=string(2,80,'^[a-z0-9]+(?:[-._][a-z0-9]+)*$');
const pt=obj({regionId:string(1,32,'^[a-z][a-z0-9-]{0,31}$'),x:integer(10,90),y:integer(10,90)});
const ofs=obj({x:integer(-12,12),y:integer(-12,12)});
const pinSchema=obj({propId:slug,revision:integer(1,Number.MAX_SAFE_INTEGER-1),sha256:string(64,64,'^[a-f0-9]{64}$')});
const puzzle=obj({id:slug,type:{const:assemblyKind},layout:obj({altar:pt,well:pt}),attachments:obj({bowl:ofs,lid:ofs}),
 bindings:obj(Object.fromEntries(roles.map(k=>[k,pinSchema])))});
export const assemblyPayloadSchemas=assemblySchemas.map((schemaVersion,i)=>obj({schemaVersion:{const:schemaVersion},projectId:{const:'binari'},
 ...(i===0?{title:string(1,100),mapId:oid,puzzleType:{const:assemblyKind},intent:string(1,2000)}:
 {assemblyId:oid,expectedRevision:integer(1,Number.MAX_SAFE_INTEGER-2),puzzles:{type:'array',minItems:0,maxItems:32,items:puzzle}})}));
