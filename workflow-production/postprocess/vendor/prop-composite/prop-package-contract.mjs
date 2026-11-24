/** Browser/Node portable shared v1 contract. Storage and renderer are injected authority ports. */
export const propPackageSchema='ngs.prop-package.v1';
export const propPackageMediaLimit=16*1024*1024;
const digestPattern=/^[a-f0-9]{64}$/u;
const requestPattern=/^request\.official\.[a-f0-9]{64}$/u;
const propPattern=/^[a-z0-9]+(?:[-._][a-z0-9]+)*$/u;
const suffix='prop.package.';
const keys=(v,fields)=>v!==null&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).length===fields.length&&
 fields.every(k=>Object.hasOwn(v,k));
const fail=code=>{throw Error(code);};
const insist=(guard,code)=>{if(!guard)fail(code);};
const ints=(v,lo,hi)=>Number.isSafeInteger(v)&&v>=lo&&v<=hi;
const hex=v=>typeof v==='string'&&v.length===64&&digestPattern.test(v);
const id=v=>typeof v==='string'&&v.length>=2&&v.length<=80&&propPattern.test(v);
const official=v=>typeof v==='string'&&v.length===81&&requestPattern.test(v);
const title=v=>typeof v==='string'&&v===v.trim()&&v.isWellFormed()&&[...v].length>=1&&[...v].length<=160&&
 !/[\p{Cc}\u2028\u2029]/u.test(v);
const utf8=new TextEncoder(),playbackProofs=new WeakMap();
const maxFrames=96, maxDecodePixels=30_000_000, maxRevision=Number.MAX_SAFE_INTEGER-1;

function canonical(v){
 if(Array.isArray(v))return '['+v.map(canonical).join(',')+']';
 if(v!==null&&typeof v==='object')return '{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+canonical(v[k])).join(',')+'}';
 return JSON.stringify(v);
}
async function sha(bytes){
 const digest=new Uint8Array(await crypto.subtle.digest('SHA-256',bytes));
 return Array.from(digest,b=>b.toString(16).padStart(2,'0')).join('');
}

function parseMedia(m,role,motion){
 const max=role==='detail'?2048:512;
 insist(keys(m,['assetId','sha256','bytes','width','height','mediaType','frameDurationsMs'])&&hex(m.sha256)&&
  m.assetId===m.sha256+'.png'&&ints(m.bytes,57,propPackageMediaLimit)&&ints(m.width,12,max)&&ints(m.height,12,max)&&
  Array.isArray(m.frameDurationsMs),'PROP_PACKAGE_MEDIA');
 const d=m.frameDurationsMs;
 if(motion==='still')insist(m.mediaType==='image/png'&&d.length===0,'PROP_PACKAGE_MOTION_MISMATCH');
 else insist(m.mediaType==='image/apng'&&d.length>=2&&d.length<=maxFrames&&d.every(n=>ints(n,10,1000))&&
  d.reduce((x,y)=>x+y,0)>=100&&d.reduce((x,y)=>x+y,0)<=10000,'PROP_PACKAGE_MOTION_MISMATCH');
 insist(m.width*m.height*Math.max(1,d.length)<=maxDecodePixels,'PROP_PACKAGE_MEDIA_BUDGET');
 const {assetId,sha256,bytes,width,height,mediaType}=m;
 return {assetId,sha256,bytes,width,height,mediaType,frameDurationsMs:[...d]};
}
function rect(v,action,m){
 if(v===null)return null;
 insist(keys(v,['action','x','y','width','height'])&&v.action===action&&ints(v.x,0,m.width-1)&&ints(v.y,0,m.height-1)&&
  ints(v.width,1,m.width-v.x)&&ints(v.height,1,m.height-v.y),'PROP_PACKAGE_HOTSPOT');
 const {x,y,width,height}=v;
 return {action,x,y,width,height};
}

function pair(value,motion){
 insist(keys(value,['detail','pixel']),'PROP_PACKAGE_MEDIA');
 const detail=parseMedia(value.detail,'detail',motion),pixel=parseMedia(value.pixel,'pixel',motion);
 insist(pixel.width<=detail.width&&pixel.height<=detail.height&&pixel.width*pixel.height<detail.width*detail.height&&
  Math.abs(pixel.width*detail.height-pixel.height*detail.width)<=Math.max(detail.width,detail.height),'PROP_PACKAGE_PIXEL_SCALE');
 insist(pixel.frameDurationsMs.length===detail.frameDurationsMs.length&&
  pixel.frameDurationsMs.every((d,i)=>d===detail.frameDurationsMs[i]),'PROP_PACKAGE_MEDIA_SYNC');
 return {detail,pixel};
}
export function parsePropPackage(v){
 insist(keys(v,['schemaVersion','projectId','propId','revision','officialRequestId','requestSha256','designContextSha256',
  'title','parentSha256','media','interaction','animations'])&&v.schemaVersion===propPackageSchema&&v.projectId==='binari'&&id(v.propId)&&
  ints(v.revision,1,maxRevision)&&official(v.officialRequestId)&&hex(v.requestSha256)&&hex(v.designContextSha256)&&title(v.title)&&
  (v.revision===1?v.parentSha256===null:hex(v.parentSha256))&&Array.isArray(v.animations)&&v.animations.length<=1024,
  'PROP_PACKAGE_INVALID');
 const media=pair(v.media,'still');
 insist(keys(v.interaction,['pixelOpen','detailTrigger']),'PROP_PACKAGE_HOTSPOT');
 const pixelOpen=rect(v.interaction.pixelOpen,'open',media.pixel),detailTrigger=rect(v.interaction.detailTrigger,'activate',media.detail);
 if(v.revision===1)insist(v.animations.length<=1,'PROP_PACKAGE_INITIAL_ANIMATION');
 const ids=new Set(),requests=new Set();
 const animations=v.animations.map(a=>{
  insist(keys(a,['id','title','officialRequestId','requestSha256','media'])&&id(a.id)&&!ids.has(a.id)&&title(a.title)&&
   official(a.officialRequestId)&&!requests.has(a.officialRequestId)&&hex(a.requestSha256),'PROP_PACKAGE_ANIMATION');
  if(v.revision===1)insist(a.id==='initial'&&a.officialRequestId===v.officialRequestId&&a.requestSha256===v.requestSha256,
   'PROP_PACKAGE_INITIAL_ANIMATION');
  ids.add(a.id);requests.add(a.officialRequestId);
  const clip=pair(a.media,'subtle');
  for(const lane of ['detail','pixel'])insist(clip[lane].width===media[lane].width&&clip[lane].height===media[lane].height,
   'PROP_PACKAGE_ANIMATION_GEOMETRY');
  return {id:a.id,title:a.title,officialRequestId:a.officialRequestId,requestSha256:a.requestSha256,media:clip};
 });
 const {schemaVersion,projectId,propId,revision,officialRequestId,requestSha256,designContextSha256,title:label,parentSha256}=v;
 return {schemaVersion,projectId,propId,revision,officialRequestId,requestSha256,designContextSha256,title:label,parentSha256,
  media,interaction:{pixelOpen,detailTrigger},animations};
}
export function canonicalPropPackage(v){
 const text=canonical(parsePropPackage(v));
 insist(utf8.encode(text).length<=512*1024,'PROP_PACKAGE_MANIFEST_BUDGET');
 return text;
}
export async function propPackageReceipt(v){return suffix+await sha(utf8.encode(canonicalPropPackage(v)));}
export function parsePropPin(v){
 insist(keys(v,['propId','revision','sha256'])&&id(v.propId)&&ints(v.revision,1,maxRevision)&&hex(v.sha256),'PROP_PACKAGE_PIN');
 return {propId:v.propId,revision:v.revision,sha256:v.sha256};
}
export function parsePuzzlePropPlacement(v){
 insist(keys(v,['propRef','placement','start','animationId'])&&keys(v.placement,['x','y'])&&[v.placement.x,v.placement.y].every(
   n=>typeof n==='number'&&Number.isFinite(n)&&n>=0&&n<=1)&&['manual','on-entry'].includes(v.start)&&
   (v.animationId===null||id(v.animationId)),'PROP_PUZZLE_BINDING');
 return {propRef:parsePropPin(v.propRef),placement:{x:v.placement.x,y:v.placement.y},start:v.start,animationId:v.animationId};
}

export function parsePropPointer(v,expectedId){
 insist(keys(v,['schemaVersion','projectId','propId','title','revision','sha256'])&&v.schemaVersion==='ngs.prop-pointer.v1'&&
  v.projectId==='binari'&&title(v.title)&&v.propId===expectedId,'PROP_PACKAGE_POINTER');
 return {schemaVersion:v.schemaVersion,projectId:v.projectId,title:v.title,...parsePropPin({propId:v.propId,revision:v.revision,sha256:v.sha256})};
}
export const propFolderPrefix='canonical-files/binari/props/';
function propFolder(propId){insist(id(propId),'PROP_PACKAGE_PATH');return propFolderPrefix+propId+'/';}
export const propPointerPath=propId=>propFolder(propId)+'metadata.json';
export function propManifestPath(propId,revision){
 insist(ints(revision,1,maxRevision),'PROP_PACKAGE_PATH');return propFolder(propId)+'releases/'+revision+'/manifest.json';
}
export function propAssetPath(propId,assetId){
 insist(typeof assetId==='string'&&assetId.length===68&&assetId.slice(-4)==='.png'&&hex(assetId.slice(0,64)),'PROP_PACKAGE_PATH');
 return propFolder(propId)+'assets/'+assetId;
}
/** Folder enumeration only. Pointers are small and never authorize reading an arbitrary asset. */
export async function listPublishedPropPointers({listPropFolders,readPointer}={}){
 insist(typeof listPropFolders==='function'&&typeof readPointer==='function','PROP_PACKAGE_PORTS');
 const values=await listPropFolders({projectId:'binari',prefix:propFolderPrefix});
 insist(Array.isArray(values)&&values.length<=10000&&new Set(values).size===values.length,'PROP_PACKAGE_FOLDERS');
 insist(values.every(id),'PROP_PACKAGE_FOLDERS');
 const out=[],sorted=[...values].sort();
 for(let i=0;i<sorted.length;i+=8){
  const page=await Promise.all(sorted.slice(i,i+8).map(async propId=>{
   const row=await readPointer({projectId:'binari',propId,path:propPointerPath(propId)});
   return row===null?null:parsePropPointer(row,propId);
  }));
  out.push(...page.filter(Boolean));
 }
 return out;
}

/** The caller still must compare-and-swap the authoritative current pointer. */
export async function validatePropSuccessor(previous,next,{operation,animationId}={}){
 const a=parsePropPackage(previous),b=parsePropPackage(next);
 insist(a.projectId===b.projectId&&a.propId===b.propId&&b.revision===a.revision+1&&b.officialRequestId!==a.officialRequestId&&
  b.parentSha256===(await propPackageReceipt(a)).slice(suffix.length),'PROP_PACKAGE_PREDECESSOR');
 const preserved=a.animations.every((clip,i)=>canonical(clip)===canonical(b.animations[i]));
 if(operation==='regenerate'){
  insist(animationId===undefined&&b.animations.length<=1,'PROP_PACKAGE_REGENERATE_ANIMATION');
  if(b.animations.length){
   const clip=b.animations[0];
   insist(clip.id==='initial'&&clip.officialRequestId===b.officialRequestId&&clip.requestSha256===b.requestSha256,
    'PROP_PACKAGE_REGENERATE_ANIMATION');
  }
 }else if(operation==='append-animation'){
  insist(id(animationId)&&preserved&&b.animations.length===a.animations.length+1&&
   ['media','title','interaction','designContextSha256'].every(k=>canonical(a[k])===canonical(b[k])),
   'PROP_PACKAGE_ANIMATION_PRESERVATION');
  const clip=b.animations[a.animations.length];
  insist(clip.id===animationId&&clip.officialRequestId===b.officialRequestId&&clip.requestSha256===b.requestSha256,
   'PROP_PACKAGE_ANIMATION_LINEAGE');
 }else fail('PROP_PACKAGE_OPERATION');
 return b;
}

const signature=[137,80,78,71,13,10,26,10];
const crcs=Uint32Array.from({length:256},(_,i)=>{
 let c=i;for(let j=0;j<8;j++)c=c&1?0xedb88320^(c>>>1):(c>>>1);return c>>>0;
});
function crc(b,start,end){
 let c=0xffffffff;for(let i=start;i<end;i++)c=crcs[(c^b[i])&255]^(c>>>8);
 return (c^0xffffffff)>>>0;
}
function pngFrames(bytes,media){
 const get=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength);
 const w=media.width,h=media.height,times=media.frameDurationsMs;
 const u32=i=>get.getUint32(i),u16=i=>get.getUint16(i);
 insist(bytes.length===media.bytes&&signature.every((v,i)=>bytes[i]===v),'PROP_PACKAGE_PNG_SIGNATURE');
 let ended=false,apng=false,seq=0,frames=[],cur=null,seenIdat=false,n=8;
 function add(part){insist(cur!==null&&part.length>0,'PROP_PACKAGE_PNG_DATA');cur.push(part);}
 for(;n<bytes.length;){
  insist(n+12<=bytes.length,'PROP_PACKAGE_PNG_CHUNK');
  const len=u32(n),start=n+8,end=n+12+len;
  insist(end<=bytes.length&&crc(bytes,n+4,end-4)===u32(end-4),'PROP_PACKAGE_PNG_CRC');
  const name=String.fromCharCode(...bytes.subarray(n+4,n+8));
  if(n===8){
   insist(name==='IHDR'&&len===13&&u32(start)===w&&u32(start+4)===h&&
    [8,6,0,0,0].every((v,i)=>bytes[start+8+i]===v),'PROP_PACKAGE_PNG_RGBA_REQUIRED');
   if(!times.length){cur=[];frames.push(cur);}
  }else if(name==='acTL'){
   insist(times.length&&len===8&&!apng&&!seenIdat&&frames.length===0&&u32(start)===times.length&&u32(start+4)===1,'PROP_PACKAGE_APNG_CONTROL');
   apng=true;
  }else if(name==='fcTL'){
   insist(len===26,'PROP_PACKAGE_APNG_FRAME');
   const delay=u16(start+20)*1000,den=u16(start+22)||100;
   insist(apng&&frames.length<times.length&&u32(start)===seq++&&
    u32(start+4)===w&&u32(start+8)===h&&u32(start+12)===0&&u32(start+16)===0&&
    delay===times[frames.length]*den&&bytes[start+24]===0&&bytes[start+25]===0&&
    (!cur||cur.length),'PROP_PACKAGE_APNG_FRAME');
   cur=[];frames.push(cur);
  }else if(name==='IDAT'){
   insist(frames.length===1,'PROP_PACKAGE_PNG_DATA');seenIdat=true;
   add(bytes.subarray(start,end-4));
  }else if(name==='fdAT'){
   insist(apng&&seenIdat&&frames.length>=2&&len>4&&u32(start)===seq++,'PROP_PACKAGE_APNG_FRAME');
   add(bytes.subarray(start+4,end-4));
  }else if(name==='IEND'){
   insist(len===0&&seenIdat&&end===bytes.length&&frames.length===(times.length||1)&&frames.every(f=>f.length),
    'PROP_PACKAGE_PNG_END');
   ended=true;
  }else{
   // Canonical worker outputs omit text/EXIF/old palette and model/sensitive metadata.
   insist(!seenIdat&&['sRGB','gAMA','cHRM','pHYs'].includes(name),'PROP_PACKAGE_PNG_CHUNK');
  }
  n=end;
 }
 insist(ended,'PROP_PACKAGE_PNG_END');
 return frames;
}
function paeth(a,b,c){
 const p=a+b-c,da=Math.abs(p-a),db=Math.abs(p-b),dc=Math.abs(p-c);
 return da<=db&&da<=dc?a:db<=dc?b:c;
}
async function verifyDecodedAlpha(parts,width,height){
 const len=height*(1+width*4),data=new Uint8Array(len);
 const stream=new Blob(parts).stream().pipeThrough(new DecompressionStream('deflate'));
 const reader=stream.getReader();let total=0;
 try{
  for(;;){
   const {done,value}=await reader.read();if(done)break;
   if(total+value.length>len){await reader.cancel();fail('PROP_PACKAGE_PNG_DECODE');}
   data.set(value,total);total+=value.length;
  }
 }catch{fail('PROP_PACKAGE_PNG_DECODE');}
 finally{reader.releaseLock();}
 insist(total===len,'PROP_PACKAGE_PNG_DECODE');
 let a=new Uint8Array(width*4),b=new Uint8Array(width*4),on=0,off=0,offset=0;
 for(let y=0;y<height;y++){
  const filter=data[offset++];insist(filter<=4,'PROP_PACKAGE_PNG_DECODE');
  for(let x=0;x<b.length;x++){
   const left=x>=4?b[x-4]:0,above=a[x],corner=x>=4?a[x-4]:0;
   const d=filter===0?0:filter===1?left:filter===2?above:filter===3?Math.floor((left+above)/2):paeth(left,above,corner);
   b[x]=(data[offset++]+d)&255;
   if((x&3)===3){if(b[x]>0)on++;else off++;}
  }
  const swap=a;a=b;b=swap;
 }
 insist(on>0&&off>0,'PROP_PACKAGE_ALPHA_CONTENT');
}
export async function verifyPropMedia(input,descriptor){
 const media=parseMedia(descriptor,'detail',descriptor?.mediaType==='image/apng'?'subtle':'still');
 insist(input instanceof Uint8Array&&input.length===media.bytes&&input.length<=propPackageMediaLimit&&
  await sha(input)===media.sha256,'PROP_PACKAGE_MEDIA_HASH');
 for(const image of pngFrames(input,media))await verifyDecodedAlpha(image,media.width,media.height);
 return input;
}

/** Resolves the exact already-approved package before any media claim is exposed to a player. */
export async function resolvePropPlayback({projectId='binari',puzzleBinding,readPackage,readAsset}={}){
 insist(projectId==='binari'&&typeof readPackage==='function'&&typeof readAsset==='function','PROP_PACKAGE_PORTS');
 const binding=parsePuzzlePropPlacement(puzzleBinding),pin=binding.propRef;
 const manifest=parsePropPackage(await readPackage({projectId,propId:pin.propId,revision:pin.revision,sha256:pin.sha256}));
 insist(manifest.projectId===projectId&&manifest.propId===pin.propId&&manifest.revision===pin.revision&&
  await propPackageReceipt(manifest)===suffix+pin.sha256,'PROP_PACKAGE_PIN_MISMATCH');
 const selected=binding.animationId===null?null:manifest.animations.find(a=>a.id===binding.animationId);
 insist(binding.animationId===null||selected,'PROP_PACKAGE_ANIMATION_MISSING');
 const materialize=async pair=>{
  const result={};
  for(const lane of ['detail','pixel']){
   const descriptor=pair[lane];
   const expected={projectId,...pin,assetId:descriptor.assetId,assetSha256:descriptor.sha256,byteLimit:descriptor.bytes};
   const raw=await readAsset(expected);
   await verifyPropMedia(raw,descriptor);
   result[lane]={descriptor,bytes:raw};
  }
  return result;
 };
 const verified={package:manifest,binding,media:await materialize(manifest.media),
  animation:selected?{id:selected.id,media:await materialize(selected.media)}:null};
 // Projection is the sealed snapshot, hence subsequent mutation of an output
 // DTO or a fabricated object cannot inject paths, commands or coordinates.
 playbackProofs.set(verified,JSON.stringify(buildPlayerProjection(verified)));
 return Object.freeze(verified);
}

/** Data-only WASM pin projection: player receives trusted media IDs not URL or filesystem paths. */
function buildPlayerProjection(verified){
 const {package:p,binding}=verified;
 const lane=id=>{
  const view=verified.media[id].descriptor, motion=verified.animation?.media[id]?.descriptor;
  const hit=id==='pixel'?p.interaction.pixelOpen:p.interaction.detailTrigger;
  return {assetId:view.assetId,animationAssetId:motion?.assetId??null,width:view.width,height:view.height,hotspot:hit};
 };
 return {schemaVersion:'ngs.prop-player-input.v1',propRef:{...binding.propRef},placement:{...binding.placement},start:binding.start,
  animationId:verified.animation?.id??null,
  playback:{frameDurationsMs:[...(verified.animation?.media.detail.descriptor.frameDurationsMs??[])],pixel:lane('pixel'),detail:lane('detail')}};
}

export function projectPropPlayer(verified){
 const sealed=playbackProofs.get(verified);
 insist(typeof sealed==='string','PROP_PACKAGE_PLAYBACK_UNVERIFIED');
 return JSON.parse(sealed);
}
