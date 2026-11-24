import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import test from 'node:test';
import {crc32,deflateSync} from 'node:zlib';
import {spawnSync} from 'node:child_process';
import {propI2VRGBStage} from './prop-i2v-rgb-stage.mjs';

const sha=v=>createHash('sha256').update(v).digest('hex');
const officialRequestId='request.official.'+'a'.repeat(64);
const taskId='workflow.task.'+sha(JSON.stringify([officialRequestId,'prop_motion']));
const scope={officialRequestId,taskId,propId:'ritual-bell',sourceSha256:'c'.repeat(64)};
const packed=n=>{const b=Buffer.alloc(4);b.writeUInt32BE(n);return b;};
const chunk=(type,b)=>{
 const body=Buffer.concat([Buffer.from(type),b]);
 return Buffer.concat([packed(b.length),body,packed(crc32(body))]);
};
function png(channelType=2){
 const head=Buffer.concat([packed(16),packed(16),Buffer.from([8,channelType,0,0,0])]);
 const row=Buffer.alloc(1+16*(channelType===2?3:4),0);row[1]=120;
 return Buffer.concat([Buffer.from('89504e470d0a1a0a','hex'),chunk('IHDR',head),
  chunk('IDAT',deflateSync(Buffer.concat(Array(16).fill(row)))),chunk('IEND',Buffer.alloc(0))]);
}

test('exact native prop RGB stage keeps frames, bounds, timed metadata, and matching typed PDS receipt',()=>{
 const raw=png(),output={width:16,height:16,fps:24,frames:Array.from({length:45},()=>raw)};
 const result=propI2VRGBStage(output,scope);
 assert.equal(result.artifact.manifest.frameDurationsMs.reduce((n,v)=>n+v),1875);
 assert.deepEqual(result.artifact.manifest.frameDurationsMs.slice(0,6),[42,41,42,42,41,42]);
 assert.equal(result.artifact.manifest.frames[44].index,44);
 assert.equal(result.artifact.manifest.frames[44].sha256,sha(raw));
 assert.equal(result.artifact.frames[44],raw.toString('base64'));
 assert.match(result.resultRef,/^prop\.i2v-rgb\.[a-f0-9]{64}$/);
 assert.deepEqual(propI2VRGBStage(output,scope),result);
});

test('transparent frames, changed scope, and non-playable frame timing do not produce requests',()=>{
 const raw=png(),output={width:16,height:16,fps:24,frames:Array.from({length:45},()=>raw)};
 assert.throws(()=>propI2VRGBStage(output,{...scope,taskId:'workflow.task.'+'2'.repeat(64)}));
 assert.throws(()=>propI2VRGBStage({...output,frames:[raw,raw]},scope));
 assert.throws(()=>propI2VRGBStage({...output,frames:[...output.frames.slice(1),png(6)]},scope));
});

test('raw artifact accepted by exact versioned PDS harness',
 {skip:!process.env.PROP_I2V_PDS_TEST_ROOT},()=>{
 const raw=png();
 const result=propI2VRGBStage({width:16,height:16,fps:24,frames:Array(45).fill(raw)},scope);
 const command=spawnSync(process.env.PROP_I2V_TEST_PYTHON??'python3',['-B','-c',`
import json,sys
from project_data_service.prop_stage_store import raw_artifact,sha,canonical
data=json.load(sys.stdin)
spec=data['artifact']['manifest']
m,frames=raw_artifact(data['artifact'],{k:spec[k] for k in ('officialRequestId','taskId','propId')})
assert len(frames)==45 and m==spec
assert 'prop.i2v-rgb.'+sha(canonical(m))==data['resultRef']
`],{input:JSON.stringify(result),encoding:'utf8',env:{...process.env,PYTHONPATH:process.env.PROP_I2V_PDS_TEST_ROOT}});
 assert.equal(command.status,0,command.stderr||command.stdout);
});
