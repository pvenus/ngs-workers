import {mapCanonical,mapFields,officialId} from './map-contract.mjs';
export const assemblyTypes=['assembly.generate','assembly.update'];
export const assemblySchemas=['ngs.assembly-generate.v1','ngs.assembly-update.v1','ngs.assembly-generate.v2','ngs.assembly-generate.v3','ngs.assembly-generate.v4'];
export const spiritAssemblyKind='binari.ritual-spirit-manifestation';
export const assemblyKind='binari.ritual-well-water-demo',assemblyStage='assembly_metadata';
export const assemblyPds='/v1/assemblies/ownership/workflow-assembly';
export const assemblyRef=(id,rev)=>'binari/cinematics/assemblies/'+id+'/'+rev+'.json';
const int=(v,lo,hi)=>Number.isSafeInteger(v)&&lo<=v&&v<=hi;
const ident=v=>typeof v==='string'&&/^[a-z0-9]+(?:[-._][a-z0-9]+)*$/u.test(v)&&v.length>=2&&v.length<=80;
const region=v=>typeof v==='string'&&/^[a-z][a-z0-9-]{0,31}$/u.test(v);
export const text=(v,max)=>typeof v==='string'&&v.isWellFormed()&&v===v.trim()&&v.length>=1&&v.length<=max&&!/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/u.test(v);
const xy=(v,lo,hi)=>int(v.x,lo,hi)&&int(v.y,lo,hi);
const notes=v=>v===''||text(v,1000);
export const validAssemblyNotes=(v,allowSpirit=false)=>Array.isArray(v)&&v.length>=1&&v.length<=8&&new Set(v.map(p=>p?.id)).size===v.length&&
 v.every(p=>ident(p?.id)&&notes(p.intent)&&(p.type===assemblyKind&&mapFields(p,['id','type','intent'])||
 allowSpirit&&p.type===spiritAssemblyKind&&mapFields(p,['id','type','intent','prop'])&&pin(p.prop)));
const station=v=>mapFields(v,['regionId','x','y'])&&region(v.regionId)&&xy(v,10,90);
const offset=v=>mapFields(v,['x','y'])&&xy(v,-12,12);
const pin=v=>mapFields(v,['propId','revision','sha256'])&&ident(v.propId)&&int(v.revision,1,Number.MAX_SAFE_INTEGER-1)&&
 typeof v.sha256==='string'&&/^[a-f0-9]{64}$/u.test(v.sha256);
const roles=['table','bowl','well','lid'];
const puzzleFields=['id','type','layout','attachments','bindings'];
const actions=['pickup','drop','activate'],modules=['inventory.move.v1','prop.interact.v1','switch.v1'];
const label=v=>typeof v==='string'&&v.length>=1&&v.length<=64&&/^[a-z][a-z0-9]*(?:[-._][a-z0-9]+)*$/u.test(v);
const stateRef=v=>mapFields(v,['role','state'])&&label(v.role)&&label(v.state);
const action=v=>mapFields(v,['kind','target','item'])&&actions.includes(v.kind)&&label(v.target)&&(v.item===null||label(v.item));
const waterRules=[
 ['inventory.move.v1','pickup','bowl',null,'bowl','altar-empty','carried-empty',[]],
 ['switch.v1','activate','lid',null,'lid','closed','open',[]],
 ['prop.interact.v1','drop','well','bowl','bowl','carried-empty','carried-filled',[['lid','open']]],
 ['inventory.move.v1','drop','altar','bowl','bowl','carried-filled','altar-filled',[]],
];
const waterRoles={bowl:['altar-empty','carried-empty','carried-filled','altar-filled'],well:['ready'],altar:['ready'],lid:['closed','open']};
export function validPuzzleDefinition(v){
 if(!mapFields(v,['schemaVersion','entities','rules','goal','solution'])||v.schemaVersion!=='ngs.puzzle-definition.v1'||
 !Array.isArray(v.entities)||v.entities.length!==4||!Array.isArray(v.rules)||v.rules.length!==4||
 !Array.isArray(v.solution)||v.solution.length!==4)return false;
 const seen=new Set();
 for(const entity of v.entities){
  if(!mapFields(entity,['role','initial','states'])||!Object.hasOwn(waterRoles,entity.role)||seen.has(entity.role)||
     entity.initial!==waterRoles[entity.role][0]||!Array.isArray(entity.states)||entity.states.length!==waterRoles[entity.role].length)return false;
  const ids=new Set();
  for(const state of entity.states){
   if(!mapFields(state,['id','held','payload'])||!waterRoles[entity.role].includes(state.id)||ids.has(state.id)||
      state.held!==(entity.role==='bowl'&&state.id.startsWith('carried-'))||
      state.payload!==(entity.role==='bowl'?state.id.split('-')[1]:state.id))return false;
   ids.add(state.id);
  }
  seen.add(entity.role);
 }
 const known=v=>stateRef(v)&&Object.hasOwn(waterRoles,v.role)&&waterRoles[v.role].includes(v.state);
 const knownAction=v=>action(v)&&Object.hasOwn(waterRoles,v.target)&&(v.item===null||Object.hasOwn(waterRoles,v.item));
 if(!known(v.goal)||v.goal.role!=='bowl'||v.goal.state!=='altar-filled'||!v.solution.every(knownAction))return false;
 const expected=new Set(waterRules.map(r=>JSON.stringify(r)));
 for(const r of v.rules){
  if(!mapFields(r,['module','action','from','to','requires'])||!modules.includes(r.module)||!knownAction(r.action)||
     !known(r.from)||!known(r.to)||r.from.role!==r.to.role||!Array.isArray(r.requires)||r.requires.length>32||!r.requires.every(known)||
     !expected.delete(JSON.stringify([r.module,r.action.kind,r.action.target,r.action.item,r.from.role,r.from.state,r.to.state,
      r.requires.map(c=>[c.role,c.state])])))return false;
 }
 const active=Object.fromEntries(v.entities.map(e=>[e.role,e.initial]));
 for(const a of v.solution){
  const r=waterRules.find(rule=>rule[1]===a.kind&&rule[2]===a.target&&rule[3]===a.item);
  if(!r||active[r[4]]!==r[5]||r[7].some(([k,value])=>active[k]!==value))return false;
  active[r[4]]=r[6];
 }
 return active.bowl==='altar-filled';
}
const optionalPuzzleFields=['scale','portalConditions','definition'];
export const portalStates=['altar-empty','carried-empty','carried-filled','altar-filled'];
const validConditions=v=>Array.isArray(v)&&v.length<=16&&new Set(v.map(c=>c?.portalId)).size===v.length&&
 v.every(c=>mapFields(c,['portalId','propRole','requiredState'])&&region(c.portalId)&&c.propRole==='bowl'&&portalStates.includes(c.requiredState));
const validScale=v=>mapFields(v,roles)&&roles.every(k=>int(v[k],10,300));
const validWellPuzzle=p=>
 mapFields(p,[...puzzleFields,...optionalPuzzleFields.filter(k=>Object.hasOwn(p??{},k))])&&
 (!Object.hasOwn(p,'definition')||validPuzzleDefinition(p.definition))&&
 (!Object.hasOwn(p,'scale')||validScale(p.scale))&&(!Object.hasOwn(p,'portalConditions')||validConditions(p.portalConditions))&&ident(p.id)&&p.type===assemblyKind&&
 mapFields(p.layout,['altar','well'])&&station(p.layout.altar)&&station(p.layout.well)&&
 mapFields(p.attachments,['bowl','lid'])&&offset(p.attachments.bowl)&&offset(p.attachments.lid)&&
 mapFields(p.bindings,roles)&&roles.every(k=>pin(p.bindings[k]))&&new Set(roles.map(k=>p.bindings[k].propId)).size===4;
const validSpiritPuzzle=p=>mapFields(p,Object.hasOwn(p??{},'scale')?[...puzzleFields,'scale']:puzzleFields)&&
 ident(p.id)&&p.type===spiritAssemblyKind&&mapFields(p.layout,['spirit'])&&station(p.layout.spirit)&&
 mapFields(p.attachments,[])&&mapFields(p.bindings,['spirit'])&&pin(p.bindings.spirit)&&
 (!Object.hasOwn(p,'scale')||mapFields(p.scale,['spirit'])&&int(p.scale.spirit,10,300));
export const validPuzzles=(v,{wellOnly=false}={})=>Array.isArray(v)&&v.length<=32&&new Set(v.map(x=>x?.id)).size===v.length&&
 v.every(p=>validWellPuzzle(p)||!wellOnly&&validSpiritPuzzle(p));
export function validateAssembly(v,type){
 const i=assemblyTypes.indexOf(type);
 const batch=i===0&&assemblySchemas.slice(2).includes(v?.schemaVersion), idx=batch?assemblySchemas.indexOf(v.schemaVersion):i;
 const fields=i===0?['title','mapId','intent',batch?'puzzles':'puzzleType']:['assemblyId','expectedRevision','puzzles'];
 if(i<0||!mapFields(v,['schemaVersion','projectId',...fields])||v.schemaVersion!==assemblySchemas[idx]||v.projectId!=='binari'||
  !(i===0?text(v.title,100)&&officialId(v.mapId)&&text(v.intent,2000)&&(batch?validAssemblyNotes(v.puzzles,idx===3)&&(idx!==4||v.puzzles.length===1):v.puzzleType===assemblyKind):
    officialId(v.assemblyId)&&int(v.expectedRevision,1,Number.MAX_SAFE_INTEGER-2)&&validPuzzles(v.puzzles,{wellOnly:true})))throw Error('NGS_ASSEMBLY_PAYLOAD_INVALID');
 return mapCanonical(v);
}
const obj=(properties,required=Object.keys(properties))=>({type:'object',additionalProperties:false,required,properties});
const string=(minLength,maxLength,pattern)=>({type:'string',minLength,maxLength,...(pattern?{pattern}:{})});
const integer=(minimum,maximum)=>({type:'integer',minimum,maximum});
const oid=string(81,81,'^request\\.official\\.[a-f0-9]{64}$');
const slug=string(2,80,'^[a-z0-9]+(?:[-._][a-z0-9]+)*$');
const pt=obj({regionId:string(1,32,'^[a-z][a-z0-9-]{0,31}$'),x:integer(10,90),y:integer(10,90)});
const ofs=obj({x:integer(-12,12),y:integer(-12,12)});
const pinSchema=obj({propId:slug,revision:integer(1,Number.MAX_SAFE_INTEGER-1),sha256:string(64,64,'^[a-f0-9]{64}$')});
const stateName=string(1,64,'^[a-z][a-z0-9]*(?:[-._][a-z0-9]+)*$');
const ruleRef=obj({role:stateName,state:stateName});
const actionSchema=obj({kind:{enum:actions},target:stateName,item:{oneOf:[stateName,{type:'null'}]}});
export const puzzleDefinitionSchema=obj({
 schemaVersion:{const:'ngs.puzzle-definition.v1'},
 entities:{type:'array',minItems:4,maxItems:4,items:obj({role:stateName,initial:stateName,
  states:{type:'array',minItems:1,maxItems:4,items:obj({id:stateName,held:{type:'boolean'},payload:stateName})}})},
 rules:{type:'array',minItems:4,maxItems:4,items:obj({module:{enum:modules},action:actionSchema,from:ruleRef,to:ruleRef,
  requires:{type:'array',maxItems:32,items:ruleRef}})},
 goal:ruleRef,solution:{type:'array',minItems:4,maxItems:4,items:actionSchema},
});
export const wellPuzzleSchema=obj({id:slug,type:{const:assemblyKind},layout:obj({altar:pt,well:pt}),attachments:obj({bowl:ofs,lid:ofs}),
 bindings:obj(Object.fromEntries(roles.map(k=>[k,pinSchema]))),scale:obj(Object.fromEntries(roles.map(k=>[k,integer(10,300)]))),
 definition:puzzleDefinitionSchema,portalConditions:{type:'array',maxItems:16,items:obj({portalId:string(1,32,'^[a-z][a-z0-9-]{0,31}$'),propRole:{const:'bowl'},
 requiredState:{enum:portalStates}})}},puzzleFields);
export const spiritPuzzleSchema=obj({id:slug,type:{const:spiritAssemblyKind},layout:obj({spirit:pt}),attachments:obj({}),
 bindings:obj({spirit:pinSchema}),scale:obj({spirit:integer(10,300)})},puzzleFields);
export const validAssemblyContent=v=>mapFields(v,['title','mapId','puzzles'])&&text(v.title,100)&&officialId(v.mapId)&&validPuzzles(v.puzzles);
export const assemblyContentSchema=obj({title:string(1,100),mapId:oid,puzzles:{type:'array',minItems:0,maxItems:32,items:{oneOf:[wellPuzzleSchema,spiritPuzzleSchema]}}});
const wellNoteSchema=obj({id:slug,type:{const:assemblyKind},intent:string(0,1000)});
const spiritNoteSchema=obj({id:slug,type:{const:spiritAssemblyKind},intent:string(0,1000),prop:pinSchema});
export const assemblyPayloadSchemas=assemblySchemas.map((schemaVersion,i)=>obj({schemaVersion:{const:schemaVersion},projectId:{const:'binari'},
 ...(i!==1?{title:string(1,100),mapId:oid,intent:string(1,2000),
  ...(i===0?{puzzleType:{const:assemblyKind}}:{puzzles:{type:'array',minItems:1,maxItems:i===4?1:8,items:i===3?{oneOf:[wellNoteSchema,spiritNoteSchema]}:wellNoteSchema}})}:
 {assemblyId:oid,expectedRevision:integer(1,Number.MAX_SAFE_INTEGER-2),puzzles:{type:'array',minItems:0,maxItems:32,items:wellPuzzleSchema}})}));
