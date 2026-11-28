// Map topology is independent of background render revisions. Coordinates are
// integer thousandths of the natural canvas, with the origin at top left.
export const mapTypes=['map.generate','map.update'];
export const mapSchemas=['ngs.map-generate.v1','ngs.map-update.v1','ngs.map-generate.v2','ngs.map-generate.v3'];
export const mapGenerateStages=['map_start','map_portals','map_collisions'];
export const mapDecorationStage='map_decorations';
export const mapStagesFor=p=>p.schemaVersion===mapSchemas[3]&&p.decorationDesign?.mode==='suggest'?
 [...mapGenerateStages,mapDecorationStage]:mapSchemas.slice(2).includes(p.schemaVersion)?mapGenerateStages:[mapStage];
export const mapStage='map_metadata',mapRoot='cinematics/maps';
export const mapPds='/v1/maps/ownership/workflow-map',mapCapability='workflow.map-metadata.v1';
export const officialId=v=>typeof v==='string'&&/^request\.official\.[a-f0-9]{64}$/.test(v);
export const backgroundId=v=>typeof v==='string'&&/^background\.[a-f0-9]{64}$/.test(v);
export const backgroundIdFromRequest=v=>officialId(v)?'background.'+v.slice(-64):null;
export const mapFields=(v,ks)=>v!==null&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).length===ks.length&&ks.every(k=>Object.hasOwn(v,k));
export const mapText=(v,max)=>typeof v==='string'&&v===v.trim()&&v.length>0&&v.length<=max&&!/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/.test(v);
export const mapPoint=v=>mapFields(v,['x','y'])&&['x','y'].every(k=>Number.isInteger(v[k])&&v[k]>=0&&v[k]<=1000);
export const mapRect=v=>mapFields(v,['x','y','width','height'])&&['x','y','width','height'].every(k=>Number.isInteger(v[k]))&&
 v.x>=0&&v.y>=0&&v.width>0&&v.height>0&&v.x+v.width<=1000&&v.y+v.height<=1000;
// Legacy colliders are unrotated rectangles. A shape and rotation must arrive together.
export const mapColliderShape=c=>c.shape??'rect';
export const mapColliderRotation=c=>c.rotation??0;
const mapCollider=c=>(mapFields(c,['id','regionId','rect'])||
 (mapFields(c,['id','regionId','rect','shape','rotation'])&&['rect','ellipse'].includes(c.shape)&&
  Number.isInteger(c.rotation)&&c.rotation>=0&&c.rotation<360))&&mapRect(c.rect);
export const regionId=v=>typeof v==='string'&&/^[a-z][a-z0-9-]{0,31}$/.test(v);
const note=v=>v===''||mapText(v,1000);
const visibility=v=>['both','pixel','cinematic'].includes(v);
export const mapPropPin=v=>mapFields(v,['propId','revision','sha256'])&&typeof v.propId==='string'&&v.propId.length>=2&&v.propId.length<=80&&
 /^[a-z0-9]+(?:[-._][a-z0-9]+)*$/.test(v.propId)&&Number.isSafeInteger(v.revision)&&v.revision>=1&&
 v.revision<Number.MAX_SAFE_INTEGER&&typeof v.sha256==='string'&&/^[a-f0-9]{64}$/.test(v.sha256);
const decorationFields=['id','regionId','prop','anchor','scale','visibility'];
export const propZMin=-1000,propZMax=1000;
export const validPropZOrder=v=>Number.isInteger(v)&&v>=propZMin&&v<=propZMax;
export const validRoleZOrder=(v,roles)=>v!==null&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).length>0&&Object.keys(v).every(k=>roles.includes(k)&&validPropZOrder(v[k]));
export const propImageScales=Object.freeze([5,10,20,30,40,50,60,70,80,90,100]);
export const validPropImageScale=v=>propImageScales.includes(v);
export const mapDecorationRotation=row=>row.rotation??0;
export const validDecorations=(v,ids)=>Array.isArray(v)&&v.length<=48&&new Set(v.map(x=>x?.id)).size===v.length&&v.every(r=>
 mapFields(r,[...decorationFields,...['rotation','flipX','flipY','zOrder','imageScalePercent'].filter(k=>r&&Object.hasOwn(r,k))])&&
 (!Object.hasOwn(r,'rotation')||(Number.isInteger(r.rotation)&&r.rotation>=0&&r.rotation<360))&&
 (!Object.hasOwn(r,'imageScalePercent')||validPropImageScale(r.imageScalePercent))&&
 (!Object.hasOwn(r,'zOrder')||validPropZOrder(r.zOrder))&&['flipX','flipY'].every(k=>!Object.hasOwn(r,k)||typeof r[k]==='boolean')&&regionId(r.id)&&ids.has(r.regionId)&&mapPropPin(r.prop)&&
 mapPoint(r.anchor)&&Number.isInteger(r.scale)&&10<=r.scale&&r.scale<=300&&visibility(r.visibility));
export const validDecorationDesign=v=>mapFields(v,['mode','notes','candidates'])&&['none','suggest'].includes(v.mode)&&note(v.notes)&&
 Array.isArray(v.candidates)&&(v.mode==='none'?v.candidates.length===0:1<=v.candidates.length&&v.candidates.length<=12)&&
 v.candidates.every(c=>mapFields(c,['prop','visibility'])&&mapPropPin(c.prop)&&visibility(c.visibility))&&
 new Set(v.candidates.map(c=>c.prop.propId)).size===v.candidates.length;
const baseRegionFields=['id','name','description','backgroundId','imageSha256'];
export const validMapPerspective=v=>v===null||(mapFields(v,['near','far'])&&
 ['near','far'].every(k=>mapFields(v[k],['point','actorHeight','viewWidth'])&&mapPoint(v[k].point)&&
 Number.isInteger(v[k].actorHeight)&&v[k].actorHeight>=10&&v[k].actorHeight<=650&&
 Number.isInteger(v[k].viewWidth)&&v[k].viewWidth>=200&&v[k].viewWidth<=1000)&&
 (v.near.point.x-v.far.point.x)**2+(v.near.point.y-v.far.point.y)**2>=100);
export const mapBackgroundIds=graph=>[...new Set(graph.regions.map(r=>r.backgroundId).filter(Boolean))];
export function validMapGraph(v,{partial=false}={}){
 const fields=['title','startRegionId','regions','connections'];
 const withStartPoint=v!==null&&typeof v==='object'&&Object.hasOwn(v,'startPoint');
 if(withStartPoint&& (partial||!mapPoint(v.startPoint)))return false;
 if(withStartPoint)fields.push('startPoint');
 if(v!==null&&typeof v==='object'&&Object.hasOwn(v,'decorations')){
  if(partial)return false;
  fields.push('decorations');
 }
 const withColliders=v!==null&&typeof v==='object'&&Object.hasOwn(v,'colliders');
 if(!mapFields(v,withColliders?[...fields,'colliders']:fields)||!mapText(v.title,100)||!Array.isArray(v.regions)||
    v.regions.length<1||v.regions.length>24||!v.regions.every(r=>(mapFields(r,baseRegionFields)||(!partial&&mapFields(r,[...baseRegionFields,'perspective'])&&validMapPerspective(r.perspective)))&&
      regionId(r.id)&&mapText(r.name,100)&&mapText(r.description,1000)&&backgroundId(r.backgroundId)&&typeof r.imageSha256==='string'&&/^[a-f0-9]{64}$/.test(r.imageSha256)))return false;
 const ids=new Set(v.regions.map(r=>r.id));
 if(Object.hasOwn(v,'decorations')&&!validDecorations(v.decorations,ids))return false;
 if(ids.size!==v.regions.length||!ids.has(v.startRegionId)||!Array.isArray(v.connections)||v.connections.length>96)return false;
 if(withColliders){
  const colliderIds=new Set();
  if(!Array.isArray(v.colliders)||v.colliders.length>96||!v.colliders.every(c=>{
   if(!mapCollider(c)||!regionId(c.id)||colliderIds.has(c.id)||
      !ids.has(c.regionId))return false;
   colliderIds.add(c.id);return true;
  }))return false;
 }
 const unique=new Set();
 return v.connections.every(e=>{
  if(!mapFields(e,['id','from','to','hit','spawn','label'])||typeof e.id!=='string'||!/^[a-z][a-z0-9-]{0,31}$/.test(e.id)||
     unique.has(e.id)||!ids.has(e.from)||!ids.has(e.to)||e.from===e.to||!mapText(e.label,100)||
     !(mapRect(e.hit)||(partial&&e.hit===null))||!(mapPoint(e.spawn)||(partial&&e.spawn===null)))return false;
  unique.add(e.id);return true;
 });
}
export function validMapRequest(v,type){
 const generate=type===mapTypes[0], v3=generate&&v?.schemaVersion===mapSchemas[3],
  staged=generate&&mapSchemas.slice(2).includes(v?.schemaVersion),i=generate?(v3?3:staged?2:0):1;
 return (generate||type===mapTypes[1])&&mapFields(v,generate?['schemaVersion','projectId','graph','intent',...(staged?['design']:[]),...(v3?['decorationDesign']:[])]:
 ['schemaVersion','projectId','graph','mapId','expectedRevision'])&&v.schemaVersion===mapSchemas[i]&&v.projectId==='binari'&&
 (!staged||(mapFields(v.design,['startNotes','portalNotes','collisionNotes','collisionMode'])&&
  [v.design.startNotes,v.design.portalNotes,v.design.collisionNotes].every(note)&&['suggest','none'].includes(v.design.collisionMode)))&&
 (!v3||validDecorationDesign(v.decorationDesign))&&validMapGraph(v.graph,{partial:generate})&&(generate?(v.graph.regions.length>=2&&v.graph.regions.length<=12&&v.graph.connections.length>0&&(v.graph.colliders?.length??0)===0&&v.graph.connections.every(e=>e.hit===null&&e.spawn===null)&&mapText(v.intent,2000)):officialId(v.mapId)&&
 Number.isSafeInteger(v.expectedRevision)&&v.expectedRevision>=1&&v.expectedRevision<Number.MAX_SAFE_INTEGER);
}
export function validMapState(v){
 return mapFields(v,['schemaVersion','projectId','mapId','revision','updatedAt','officialRequestId','graph'])&&
 v.schemaVersion==='ngs.map-metadata.v1'&&v.projectId==='binari'&&officialId(v.mapId)&&officialId(v.officialRequestId)&&
 Number.isSafeInteger(v.revision)&&v.revision>=1&&Number.isSafeInteger(v.updatedAt)&&v.updatedAt>=0&&validMapGraph(v.graph);
}
export const mapCanonical=v=>JSON.stringify(v,(_k,x)=>x&&typeof x==='object'&&!Array.isArray(x)?
 Object.fromEntries(Object.keys(x).sort().map(k=>[k,x[k]])):x);
export function applyMapCoordinates(graph,coordinates){
 const pending=graph.connections.filter(e=>e.hit===null||e.spawn===null);
 if(!mapFields(coordinates,['coordinates'])||!Array.isArray(coordinates.coordinates)||coordinates.coordinates.length!==pending.length)
  throw Error('MAP_MODEL_COORDINATES_INVALID');
 const values=new Map();
 for(const e of coordinates.coordinates){
  if(!mapFields(e,['id','hit','spawn'])||values.has(e.id)||!mapRect(e.hit)||!mapPoint(e.spawn))throw Error('MAP_MODEL_COORDINATES_INVALID');
  values.set(e.id,e);
 }
 if(pending.some(e=>!values.has(e.id)))throw Error('MAP_MODEL_COORDINATES_INVALID');
 const result={...graph,connections:graph.connections.map(e=>({...e,hit:e.hit??values.get(e.id).hit,spawn:e.spawn??values.get(e.id).spawn}))};
 if(!validMapGraph(result))throw Error('MAP_MODEL_COORDINATES_INVALID');return result;
}
export function mapGraphMatches(source,result){
 if(!validMapGraph(result)||result.connections.length!==source.connections.length)return false;
 const masked={...result,connections:result.connections.map((e,i)=>({...e,
  hit:source.connections[i].hit===null?null:e.hit,spawn:source.connections[i].spawn===null?null:e.spawn}))};
 return mapCanonical(masked)===mapCanonical(source);
}

export async function hashBrowserMap(project,queueId){
 const data=new TextEncoder().encode(mapCanonical(['request.official.v1',queueId.startsWith('ngs.space.')?'space':'local',project,queueId]));
 const bytes=new Uint8Array(await crypto.subtle.digest('SHA-256',data));
 return 'request.official.'+Array.from(bytes,x=>x.toString(16).padStart(2,'0')).join('');
}
