import assert from 'node:assert/strict';
import {access,lstat,mkdtemp,readFile,realpath,rm,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {dirname,join} from 'node:path';
import test from 'node:test';
import {createRepresentativeRenderer} from './representative-renderer.mjs';
import {canonical,sha} from './vendor/planner-f24d23a/envelope.mjs';

const scopes=['binari/concept','binari/npcs/npc.ch1_dajeong/planning'];
function sourceSet(rows){
 const files=rows.map(([scope,path,text])=>({scope,path,text,size:Buffer.byteLength(text),sha256:sha(text)}))
  .sort((a,b)=>a.scope===b.scope?(a.path<b.path?-1:a.path>b.path?1:0):a.scope<b.scope?-1:1);
 return {scopes,files,manifestSha256:sha(canonical(files.map(({text,...f})=>f)))};
}
const json=(scope,path,doc)=>[scope,path,JSON.stringify(doc)];
const field=(section,id,value='절제된 색')=>json(scopes[0],`sections/${section}/fields/${id}.json`,
 {id,label:id,value,sources:['PRIVATE-FIELD-SOURCES']});
const metadata=(section,fieldIds)=>json(scopes[0],`sections/${section}/metadata.json`,
 {id:section,title:section,summary:{sentence:'tone',keywords:[]},fieldIds});
const characterIds=['identity','persona','mutable'],expressionIds=['narrativeTone','visualStyle','visualProhibitions'];
const conceptRows=[
 metadata('character',characterIds),...characterIds.map(id=>field('character',id)),
 metadata('expression',expressionIds),...expressionIds.map(id=>field('expression',id)),
];
const plan=(hex,name,content,planningRole,bodyId)=> {
 const id='category.preset.'+hex.repeat(32);
 return json(scopes[1],id+'.json',{id:bodyId??id,name,content,planningRole});
};
const input={character:{npcId:'npc.ch1_dajeong',name:'다정',slug:'ch1_dajeong',role:'마을 주민',
 actId:'act01',chapterId:'chapter01',unapproved:'should not be transmitted'},
 intent:'치마가 무릎과 종아리를 덮고 신만 보인다.',sourceRefs:['concept/npc'],presetMetadata:'fresh preset metadata',
 sources:sourceSet([...conceptRows,plan('a','대표 포즈 기획','이번 프리셋: 같은 NPC의 대표 기획 슬롯.','portrait.representative')])};
const expected='### 대표 포즈\n긴 치마와 단정한 실루엣.';
async function fixture(t,body){
 const directory=await mkdtemp(join(tmpdir(),'representative-fixture-'));
 t.after(()=>rm(directory,{force:true,recursive:true}));
 const binary=join(directory,'codex-fixture');
 const record=join(directory,'record');
 await writeFile(binary,'#!'+process.execPath+'\n'+
  "const fs=require('node:fs');const argv=process.argv.slice(2);const cwd=process.cwd();"+
  "const schema=JSON.parse(fs.readFileSync(argv[argv.indexOf('--output-schema')+1], 'utf8'));"+
  "const prompt=fs.readFileSync(0,'utf8');"+
  'fs.writeFileSync('+JSON.stringify(record)+',JSON.stringify({argv,cwd,schema,prompt}));'+body,{mode:0o700});
 return {binary,read:async()=>JSON.parse(await readFile(record,'utf8'))};
}
const emit=content=>'console.log(JSON.stringify({type:"item.completed",item:{type:"agent_message",text:JSON.stringify('+JSON.stringify(content)+')}}));console.log(JSON.stringify({type:"turn.completed"}));';
test('runs Codex once in private scratch with the approved scope and validates stdout JSON',async t=>{
 const {binary,read}=await fixture(t,emit({content:expected}));
 const render=createRepresentativeRenderer({codexPath:binary});
 assert.equal(await render(input),expected);
 const {argv,cwd,schema,prompt}=await read();
 assert.deepEqual(argv.slice(0,3),['-a','never','exec']);
 for(const flag of ['--ephemeral','--ignore-user-config','--ignore-rules','read-only','shell_tool','unified_exec','apps','plugins','--json'])assert(argv.includes(flag));
 assert.equal(argv.some(value=>value.includes(input.intent)),false);
 assert.equal(schema.additionalProperties,false);
 assert.match(prompt,/대표|representative/);
 assert.match(prompt,/치마가 무릎과 종아리를 덮고 신만 보인다/);
 assert.doesNotMatch(prompt,/should not be transmitted/);
 assert.match(prompt,/절제된 색/);assert.doesNotMatch(prompt,/이번 프리셋/);assert.match(prompt,/fresh preset metadata/);
 await assert.rejects(access(cwd),{code:'ENOENT'});
});
test('rejects non-allowlisted tool events, unsupported output and incomplete turns',async t=>{
 for(const body of [
  emit({content:expected,extra:true}),emit({content:' '}),
  'console.log(JSON.stringify({type:"item.completed",item:{type:"command_execution"}}));'+emit({content:expected}),
  'console.log(JSON.stringify({type:"item.completed",item:{type:"agent_message",text:"bad"}}));',
 ]){
  const {binary,read}=await fixture(t,body);
  await assert.rejects(createRepresentativeRenderer({codexPath:binary})(input),/REPRESENTATIVE_CODEX_OUTPUT_INVALID/);
  await assert.rejects(access((await read()).cwd),{code:'ENOENT'});
 }
});
test('kills timeout, cleans scratch and returns only fixed-code failure',async t=>{
 const {binary,read}=await fixture(t,'process.stderr.write("do not echo this");setInterval(()=>{},1000);');
 await assert.rejects(createRepresentativeRenderer({codexPath:binary,timeoutMs:1000})(input),{message:'REPRESENTATIVE_CODEX_TIMEOUT'});
 await assert.rejects(access((await read()).cwd),{code:'ENOENT'});
 await assert.rejects(createRepresentativeRenderer({codexPath:binary})({...input,intent:''}),{message:'REPRESENTATIVE_INPUT_INVALID'});
});

test('opt-in audit captures separate owner-only input, exact transmitted prompt, and validated output without overwrite',async t=>{
 const {binary,read}=await fixture(t,emit({content:expected}));
 const auditDirectory=join(await realpath(dirname(binary)),'audit');
 const render=createRepresentativeRenderer({codexPath:binary,auditDirectory});
 assert.equal(await render(input),expected);
 assert.equal((await lstat(auditDirectory)).mode&0o777,0o700);
 for(const name of ['input.json','prompt.txt','result.json'])assert.equal((await lstat(join(auditDirectory,name))).mode&0o777,0o600);
 assert.equal(await readFile(join(auditDirectory,'prompt.txt'),'utf8'),(await read()).prompt);
 const data=JSON.parse(await readFile(join(auditDirectory,'input.json'),'utf8'));
 assert.deepEqual(Object.keys(data),['character','intent','sourceRefs','presetMetadata','sources','sourceSelection']);
 assert.equal(data.character.unapproved,undefined);
 assert.deepEqual(JSON.parse(await readFile(join(auditDirectory,'result.json'),'utf8')),{content:expected});
 await assert.rejects(render({...input,sources:{}}),/WORKFLOW_PLAN_SOURCE_SHAPE/);
 await assert.rejects(render({...input,intent:'Do not replace this audit.'}),/REPRESENTATIVE_AUDIT_UNAVAILABLE/);
 assert.equal(await readFile(join(auditDirectory,'prompt.txt'),'utf8'),(await read()).prompt);
});


test('complete 68-file inventory is traced; all indexed character/expression files and no NPC planning are transmitted',async t=>{
 const {binary,read}=await fixture(t,emit({content:expected}));
 const auditDirectory=join(await realpath(dirname(binary)),'audit');
 const additions=['item.add.dbc17731564d7d92798565a8','item.add.aab318d68a6613e5fa80e2d1','item.add.0f6c59120e48b759dfe133fd'];
 const rows=[
  ...conceptRows.filter(row=>row[1]!=='sections/expression/metadata.json'),
  metadata('expression',[...expressionIds,...additions]),
  ...additions.map(id=>field('expression',id,'EXACT-ACTIVE-SECTION-CARD')),
  ...Array.from({length:52},(_,i)=>json(scopes[0],'nested/'+String(i).padStart(2,'0')+'.json',{concept:'UNSELECTED'+i+'x'.repeat(1460)})),
  [scopes[0],'sections/character/fields/detached.json','{unparsed-DETACHED'],
  plan('a','대표 포즈 기획','SECRET-REPRESENTATIVE','portrait.representative'),
  plan('b','인물 개요','SECRET-OVERVIEW'),
  plan('c','포트레이트용 아트','SECRET-PORTRAIT-ART'),
  [scopes[1],'unparsed.json','{SECRET-NPC-MALFORMED'],
 ];
 const sources=sourceSet(rows);
 assert.equal(await createRepresentativeRenderer({codexPath:binary,auditDirectory})({...input,sources}),expected);
 const {prompt}=await read();
 const data=JSON.parse(prompt.split('\n').at(-2));
 assert.equal(data.sources.files.length,11);assert.equal(data.sources.manifestSha256,sources.manifestSha256);
 assert.deepEqual(data.sources.scopes,[scopes[0]]);
 assert(!Object.hasOwn(data,'sourceSelection'));
 for(const id of additions)assert(prompt.includes(id));
 assert.match(prompt,/EXACT-ACTIVE-SECTION-CARD/);
 assert.doesNotMatch(prompt,/UNSELECTED|SECRET-|DETACHED|PRIVATE-FIELD-SOURCES|category\.preset/);
 const audit=await readFile(join(auditDirectory,'input.json'),'utf8');
 assert.doesNotMatch(audit,/UNSELECTED|SECRET-|DETACHED|PRIVATE-FIELD-SOURCES/);
 const inventory=JSON.parse(audit).sourceSelection.inventory;
 assert.equal(inventory.length,68);
 assert.deepEqual(inventory.map(({selected,reason,...entry})=>entry),sources.files.map(({text,...entry})=>entry));
 assert.equal(inventory.filter(row=>row.selected).length,11);
 assert.equal(inventory.filter(row=>row.reason==='npc-planning-excluded').length,4);
 assert.equal(inventory.find(row=>row.path.endsWith('/detached.json')).reason,'not-in-section-index');
 const bigger=sourceSet([...conceptRows.filter(row=>!row[1].endsWith('/identity.json')),
  field('character','identity','z'.repeat(200_000)),rows.at(-1)]);
 await assert.rejects(createRepresentativeRenderer({codexPath:binary})({...input,sources:bigger}),/WORKFLOW_PLAN_PROMPT_TOO_LARGE/);
});

test('selected section index, formats and file identities fail closed',async t=>{
 const {binary}=await fixture(t,emit({content:expected}));
 const rows=input.sources.files.map(({scope,path,text})=>[scope,path,text]);
 const render=createRepresentativeRenderer({codexPath:binary});
 const change=(suffix,replace)=>sourceSet(rows.map(row=>row[1]===suffix?replace(row):row));
 const meta='sections/character/metadata.json',id='sections/character/fields/identity.json';
 await assert.rejects(render({...input,sources:sourceSet(rows.filter(row=>row[1]!==id))}),/WORKFLOW_PLAN_CONCEPT_REQUIRED/);
 await assert.rejects(render({...input,sources:change(meta,()=>metadata('character',['identity','identity']))}),/WORKFLOW_PLAN_CONCEPT_SECTION/);
 await assert.rejects(render({...input,sources:change(meta,()=>metadata('character',['..\/expression']))}),/WORKFLOW_PLAN_CONCEPT_SECTION/);
 await assert.rejects(render({...input,sources:change(id,([scope,path])=>[scope,path,'{"id":"other"}'])}),/WORKFLOW_PLAN_CONCEPT_IDENTITY/);
 await assert.rejects(render({...input,sources:change(id,([scope,path])=>[scope,path,'{'])}),/WORKFLOW_PLAN_SOURCE_JSON_INVALID/);
});
