import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import {buildPropGeneratePlan,propKeys,propRef} from './vendor/prop-composite/prop-generate-plan.mjs';
import {validatePropGenerate,validatePropRegenerate,validatePropAnimation} from './vendor/prop-composite/prop-generate-envelope.mjs';

const production=new URL('../',import.meta.url);
const read=path=>readFileSync(new URL(path,production),'utf8');
const dirs=['document/vendor/prop-composite-v1','image/vendor/prop-composite-v1','animation/vendor/prop-composite','postprocess/vendor/prop-composite'];

test('resident prop request and DAG vendor copies agree across worker lanes',()=>{
 const file='prop-generate-envelope.mjs';
 const legacy=read(dirs[0]+'/'+file);
 for(const dir of dirs.slice(1))assert.equal(read(dir+'/'+file),legacy);
 const plan='prop-generate-plan.mjs';
 const exact=read(dirs[1]+'/'+plan);
 for(const dir of dirs.slice(2))assert.equal(read(dir+'/'+plan).replace('../planner-f24d23a/envelope.mjs','./envelope.mjs'),exact);
});
test('only opted-in generation and regeneration plans include spirit; legacy and append lanes remain unchanged',()=>{
 const base={schemaVersion:'ngs.prop-generate.v1',projectId:'binari',propId:'ritual-bell',intent:'A bell.',
  designContextSha256:'2'.repeat(64)};
 const source={revision:1,sha256:'3'.repeat(64)};
 const officialRequestId='request.official.'+'1'.repeat(64);
 const plan=(payload,requestType)=>buildPropGeneratePlan({officialRequestId,requestType,payload});
 assert.doesNotThrow(()=>validatePropGenerate(base,'binari'));
 assert.deepEqual(plan(base,'prop.generate').map(row=>row.key),[propKeys.design,propKeys.base,propKeys.detail,propKeys.pixel,propKeys.publish]);
 for(const [type,payload] of [['prop.generate',base],['prop.regenerate',{...base,schemaVersion:'ngs.prop-regenerate.v1',source}]]){
  const check=type==='prop.generate'?validatePropGenerate:validatePropRegenerate;
  const opted={...payload,presentationProfile:'spirit-v1'};
  assert.doesNotThrow(()=>check(opted,'binari'));
  assert.throws(()=>check({...opted,presentationProfile:'typo'},'binari'));
  const tasks=plan({...opted,motion:'subtle'},type),detail=tasks.find(a=>a.key===propKeys.detail),spirit=tasks.find(a=>a.key===propKeys.spirit);
  assert.equal(spirit.type,type+'.spirit');
  assert.equal(spirit.dependsOn,detail.id);
  assert.equal(spirit.input.detailTaskId,detail.id);
  assert.ok(tasks.every(task=>task.input.presentationProfile==='spirit-v1'));
  assert.ok(tasks.at(-1).additionalDependencies.includes(spirit.id));
  assert.equal(propRef(propKeys.spirit,source.sha256),'prop.spirit-rgba.'+source.sha256);
 }
 const animated={schemaVersion:'ngs.prop-animation-generate.v1',projectId:'binari',propId:'ritual-bell',source,motionId:'wave',prompt:'Sway'};
 assert.doesNotThrow(()=>validatePropAnimation(animated,'binari'));
 assert.throws(()=>validatePropAnimation({...animated,presentationProfile:'spirit-v1'},'binari'));
 assert.deepEqual(plan(animated,'prop.animation.generate').map(row=>row.key),[propKeys.motion,propKeys.animDetail,propKeys.animPixel,propKeys.publish]);
});
