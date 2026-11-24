/** Exact media-verification projection of the NGS prop package contract. */
export const propPackageMediaLimit=16*1024*1024;
const digestPattern=/^[a-f0-9]{64}$/u;
const keys=(v,fields)=>v!==null&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).length===fields.length&&
 fields.every(k=>Object.hasOwn(v,k));
const fail=code=>{throw Error(code);};
const insist=(guard,code)=>{if(!guard)fail(code);};
const ints=(v,lo,hi)=>Number.isSafeInteger(v)&&v>=lo&&v<=hi;
const hex=v=>typeof v==='string'&&v.length===64&&digestPattern.test(v);
const maxFrames=96, maxDecodePixels=30_000_000;
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
