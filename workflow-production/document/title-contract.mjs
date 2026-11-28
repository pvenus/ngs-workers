/** Portable title metadata. Asset locations and machine-specific scene paths are not author inputs. */
export const titleType='title.generate';
export const titleGenerateSchema='ngs.title-generate.v1';
export const titleSchema='ngs.title-meta.v1';
export const titleGroup='ui/titles';
export const titleUpdateRoot='canonical-files/binari/'+titleGroup;
export const officialTitleId=v=>typeof v==='string'&&v.length===81&&/^request\.official\.[a-f0-9]{64}$/u.test(v);
const audioId=v=>officialTitleId(v)||typeof v==='string'&&v.length===77&&/^audio\.import\.[a-f0-9]{64}$/u.test(v);
export const titleHash=v=>typeof v==='string'&&v.length===64&&/^[a-f0-9]{64}$/u.test(v);
const object=(v,keys)=>v!==null&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).length===keys.length&&keys.every(k=>Object.hasOwn(v,k));
const text=(v,max,min=1)=>typeof v==='string'&&[...v].length>=min&&[...v].length<=max&&v.isWellFormed()&&v===v.trim()&&!/[\u0000-\u001f\u007f-\u009f\u2028\u2029]/u.test(v);
const number=(v,min,max)=>typeof v==='number'&&Number.isFinite(v)&&v>=min&&v<=max;
const integer=(v,min,max)=>Number.isSafeInteger(v)&&number(v,min,max);
const checkEnum=(v,...values)=>values.includes(v);
const ease=v=>checkEnum(v,'linear','smooth');
const fit=v=>checkEnum(v,'cover','contain');
const pin=(v,check=officialTitleId)=>object(v,['id','sha256'])&&check(v.id)&&titleHash(v.sha256);
const opt=(v,p)=>v===null||p(v);
const typography=(v,max=200)=>object(v,['mode','fontSize','color','letterSpacing','alignment'])&&
 checkEnum(v.mode,'image','text','overlay')&&integer(v.fontSize,1,max)&&typeof v.color==='string'&&
 /^#[a-fA-F0-9]{6}(?:[a-fA-F0-9]{2})?$/u.test(v.color)&&[7,9].includes(v.color.length)&&
 number(v.letterSpacing,-20,50)&&checkEnum(v.alignment,'left','center','right');
const limits={
 leftRatio:[0,1],topRatio:[0,1],logoWidthRatio:[0,1],buttonWidthRatio:[0,1],
 maxLogoWidth:[0,4096],fallbackLogoHeight:[0,1024],minButtonWidth:[0,4096],maxButtonWidth:[0,4096],
 buttonHeightRatio:[0,5],buttonSpacing:[0,512],logoSpacing:[0,512],leftSafePadding:[0,512],topSafePadding:[0,512],
 bottomSafePadding:[0,512],revealRisePixels:[0,512],replayWidth:[0,2048],replayHeight:[0,1024],replayBottom:[0,1024],
};
const layoutKeys=Object.keys(limits);
const layout=v=>object(v,layoutKeys)&&layoutKeys.every(k=>number(v[k],...limits[k]))&&
 ['logoWidthRatio','buttonWidthRatio','maxLogoWidth','maxButtonWidth','buttonHeightRatio'].every(k=>v[k]>0)&&v.maxButtonWidth>=v.minButtonWidth;
export function validTitleMeta(v){
 if(!object(v,['schemaVersion','displayName','logo','background','bgm','buttons','titleTypography','opening','closing','layout'])||
    v.schemaVersion!==titleSchema||!text(v.displayName,80)||!opt(v.logo,pin)||!typography(v.titleTypography)||
    !layout(v.layout))return false;
 const {background:b,opening:o,closing:c,bgm:m,buttons}=v;
 return object(b,['sequence','fit','entryFit','entryFitSeconds','entryFitCurve','loopMode','framePercent','loopSpeed','fadeSeconds'])&&
  pin(b.sequence)&&fit(b.fit)&&fit(b.entryFit)&&number(b.entryFitSeconds,0,20)&&ease(b.entryFitCurve)&&
  checkEnum(b.loopMode,'loop','pingpong')&&integer(b.framePercent,0,100)&&number(b.loopSpeed,.05,8)&&number(b.fadeSeconds,0,20)&&
  object(o,['fadeSeconds','hudRevealSeconds','fadeCurve','hudRevealCurve'])&&number(o.fadeSeconds,0,20)&&number(o.hudRevealSeconds,0,10)&&ease(o.fadeCurve)&&ease(o.hudRevealCurve)&&
  object(c,['hudHideSeconds','hudHideCurve','fadeCurve','replayText'])&&number(c.hudHideSeconds,0,10)&&ease(c.hudHideCurve)&&ease(c.fadeCurve)&&text(c.replayText,50,0)&&
  opt(m,m=>object(m,['asset','volume','loopMode'])&&pin(m.asset,audioId)&&number(m.volume,0,1)&&checkEnum(m.loopMode,'loop','once'))&&
  Array.isArray(buttons)&&buttons.length>=1&&buttons.length<=2&&buttons[0]?.action==='enter'&&
  (buttons.length===1||buttons[1]?.action==='quit')&&
  buttons.every(button=>object(button,['action','text','asset','typography'])&&text(button.text,50)&&opt(button.asset,pin)&&typography(button.typography));
}
export function validTitleGenerate(v){
 return object(v,['schemaVersion','projectId','title','intent','content'])&&v.schemaVersion===titleGenerateSchema&&v.projectId==='binari'&&
  text(v.title,80)&&text(v.intent,1200)&&validTitleMeta(v.content)&&v.title===v.content.displayName;
}
export function validTitlePublication(v,id){
 return object(v,['id','revision','officialRequestId','content'])&&
  v.id===id&&officialTitleId(v.id)&&
  integer(v.revision,1,Number.MAX_SAFE_INTEGER-1)&&officialTitleId(v.officialRequestId)&&validTitleMeta(v.content);
}

export const titleTask='title_metadata';
export const titlePds='/v1/titles/ownership/workflow-title';
export const titleCapability='workflow.title.v1';
export const titleRef=(id,revision)=>'binari/ui/titles/'+id+'/.revisions/'+revision+'.json';
const canonical=v=>JSON.stringify(v,(_k,x)=>x&&typeof x==='object'&&!Array.isArray(x)?Object.fromEntries(Object.keys(x).sort().map(k=>[k,x[k]])):x);
export function validateTitleGenerate(v){
 if(!validTitleGenerate(v))throw Error('NGS_TITLE_PAYLOAD_INVALID');
 return canonical(v);
}

const obj=properties=>({type:'object',properties,required:Object.keys(properties),additionalProperties:false});
const en=(...values)=>({enum:values});
const num=(minimum,maximum,type='number')=>({type,minimum,maximum});
const txt=(maxLength,minLength=1)=>({type:'string',minLength,maxLength});
const either=(...values)=>({oneOf:values});
const nullable=v=>either({type:'null'},v);
const officialPattern='^request[.]official[.][a-f0-9]{64}$';
const pinSpec=(pattern=officialPattern)=>obj({id:{type:'string',pattern},sha256:{type:'string',pattern:'^[a-f0-9]{64}$'}});
const fitSpec=en('cover','contain'),easeSpec=en('linear','smooth');
const typo=obj({mode:en('image','text','overlay'),fontSize:num(1,200,'integer'),color:{type:'string',pattern:'^#[a-fA-F0-9]{6}([a-fA-F0-9]{2})?$'},letterSpacing:num(-20,50),alignment:en('left','center','right')});
export const titleMetaPayloadSchema=obj({
 schemaVersion:{const:titleSchema},displayName:txt(80),logo:nullable(pinSpec()),
 background:obj({sequence:pinSpec(),fit:fitSpec,entryFit:fitSpec,entryFitSeconds:num(0,20),entryFitCurve:easeSpec,loopMode:en('loop','pingpong'),framePercent:num(0,100,'integer'),loopSpeed:num(.05,8),fadeSeconds:num(0,20)}),
 bgm:nullable(obj({asset:pinSpec('^(request[.]official|audio[.]import)[.][a-f0-9]{64}$'),volume:num(0,1),loopMode:en('loop','once')})),
 buttons:{type:'array',minItems:1,maxItems:2,items:obj({action:en('enter','quit'),text:txt(50),asset:nullable(pinSpec()),typography:typo})},
 titleTypography:typo,
 opening:obj({fadeSeconds:num(0,20),hudRevealSeconds:num(0,10),fadeCurve:easeSpec,hudRevealCurve:easeSpec}),
 closing:obj({hudHideSeconds:num(0,10),hudHideCurve:easeSpec,fadeCurve:easeSpec,replayText:txt(50,0)}),
 layout:obj(Object.fromEntries(Object.entries(limits).map(([k,v])=>[k,num(...v)]))),
});
export const titleGeneratePayloadSchema=obj({schemaVersion:{const:titleGenerateSchema},projectId:{const:'binari'},title:txt(80),intent:txt(1200),content:titleMetaPayloadSchema});
