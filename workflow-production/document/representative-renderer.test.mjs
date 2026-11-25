import assert from 'node:assert/strict';
import {access,lstat,mkdtemp,readFile,realpath,rm,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {dirname,join} from 'node:path';
import test from 'node:test';
import {authoringPrinciples,buildAuthoringPrompt} from './authoring-prompt.mjs';
import {planningHeadings} from './planning-markdown.mjs';
import {createRepresentativeRenderer} from './representative-renderer.mjs';
import {canonical,sha} from './vendor/planner-89c7568/envelope.mjs';

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
const assertMandatory=prompt=>{
 assert(prompt.startsWith(authoringPrinciples+'\n'));
 assert.match(prompt,/Write in Markdown\. Organize and fill every predefined main item/);
 assert.equal(prompt.split('Mandatory document authoring rules:').length,2);
};
const emit=content=>'console.log(JSON.stringify({type:"item.completed",item:{type:"agent_message",text:JSON.stringify('+JSON.stringify(content)+')}}));console.log(JSON.stringify({type:"turn.completed"}));';
test('runs Codex once in private scratch with the approved scope and validates stdout JSON',async t=>{
 const {binary,read}=await fixture(t,emit({content:expected}));
 const render=createRepresentativeRenderer({codexPath:binary});
 assert.equal(await render(input),expected);
 const {argv,cwd,schema,prompt}=await read();
 assertMandatory(prompt);
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
 assertMandatory(prompt);
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

test('fresh portrait-art renderer receives exact overview and proposal status and enforces its first production block',async t=>{
 const operation='npc.plan-portrait-art',headings=planningHeadings[operation];
 const content='# 공통 외형\n'+headings.map(h=>'## '+h+'\n한 명의 인물 전신과 긴 치마').join('\n');
 const {binary,read}=await fixture(t,emit({content}));
 const withProposal=conceptRows.map(([scope,path,text])=>path.endsWith('identity.json')?
   [scope,path,JSON.stringify({...JSON.parse(text),status:'proposal'})]:[scope,path,text]);
 const options={...input,overviewContent:'# 인물 개요\n인물과 역할 정식 입력',sources:sourceSet([
  ...withProposal,plan('a','대표 포즈 기획','DO-NOT-TRANSMIT','portrait.representative')])};
 const render=createRepresentativeRenderer({codexPath:binary,operation});
 assert.equal(await render(options),content);
 const {prompt}=await read(),data=JSON.parse(prompt.split('\n').at(-2));
 for(const h of headings)assert(prompt.includes('## '+h));
 assert.match(prompt,/isolated full-body game character on transparent background/);
 assert.equal(data.overviewContent,options.overviewContent);
 assert.equal(JSON.parse(data.sources.files.find(f=>f.path.endsWith('identity.json')).normalizedText).status,'proposal');
 assert.doesNotMatch(prompt,/DO-NOT-TRANSMIT/);
 const bad=await fixture(t,emit({content:content.replace('## 이미지 제작 지시','## 다른 제목')}));
 await assert.rejects(createRepresentativeRenderer({codexPath:bad.binary,operation})(options),/WORKFLOW_PLAN_MARKDOWN_CONTRACT/);
});

test('Master Concept authoring invokes the owner CLI on the exact selected outline only',async t=>{
 const {binary,read}=await fixture(t,emit({content:'# 한국 국악풍 사운드\n\n## 편성\n\n대금을 사용한다.'}));
 const render=createRepresentativeRenderer({codexPath:binary,operation:'concept.category-generate'});
 const draft={categoryId:'category.add.'+'d'.repeat(32),title:'한국 국악풍 사운드',outline:'피리 대신 대금 중심',
  requirements:'악기 이름 명시'};
 const result=await render(draft);
 assert.match(result,/^# 한국 국악풍 사운드/);
 const {prompt}=await read(),input=JSON.parse(prompt.trimEnd().split('\n').at(-1));
 assert.deepEqual(input,draft);
 assert.match(prompt,/single document|single string|one full/);
 await assert.rejects(render({...draft,oldSource:'must not be read'}),/WORKFLOW_CONCEPT_PROMPT_INPUT_INVALID/);
});

test('concept update transmits the complete exact source and a scoped overwrite prompt, no outline or tool access',async t=>{
 const title='표현 원칙',source='# '+title+'\n\n## 색감\n\n전체 원문 첫 줄.\n\n## 마지막\n\n마지막 원문.\n';
 const content=source.replace('색감','새 색감');
 const {binary,read}=await fixture(t,emit({content}));
 const render=createRepresentativeRenderer({codexPath:binary,operation:'concept.category-generate'});
 const request={categoryId:'expression',title,requirements:'색감 제목만 바꿔 줘.',source};
 assert.equal(await render(request),content);
 const {prompt,argv}=await read();
 assertMandatory(prompt);
 assert.match(prompt,/Return the entire finished Markdown, not a patch/);
 assert.match(prompt,/Do not use tools/);
 assert.deepEqual(JSON.parse(prompt.trimEnd().split('\n').at(-1)),request);
 assert.equal(argv.some(value=>value.includes(source)),false);
 await assert.rejects(render({...request,outline:'unrelated'}),/WORKFLOW_CONCEPT_PROMPT_INPUT_INVALID/);
});

test('concept add schema exposes only ordered body fields, never headings or whole Markdown',async t=>{
 const bodies={section_1:'본문 첫째',section_2:'본문 둘째'};
 const {binary,read}=await fixture(t,emit(bodies));
 const render=createRepresentativeRenderer({codexPath:binary,operation:'concept.category-generate'});
 const input={categoryId:'expression',title:'표현 원칙',outline:'색감과 구도',requirements:'',requiredSections:['색감','구도']};
 assert.deepEqual(await render(input),bodies);
 const {schema,prompt}=await read();
 assertMandatory(prompt);
 assert.deepEqual(schema,{type:'object',properties:{section_1:{type:'string'},section_2:{type:'string'}},
  required:['section_1','section_2'],additionalProperties:false});
 assert.deepEqual(JSON.parse(prompt.trimEnd().split('\n').at(-1)),input);
 assert.match(prompt,/Each field contains only the Markdown body for its assigned section\./);
 assert.match(prompt,/title, headings, and ordering are chosen externally/);
 assert.match(prompt,/basic Markdown for paragraphs, lists and emphasis/);
 assert.match(prompt,/Do not include headings, document wrappers/);
 assert.doesNotMatch(prompt,/Do not output.*Markdown syntax/);
});

test('single NPC update CLI requests full Markdown and exact role headings; only approved role references plus existing Master inputs',async t=>{
 const role='portrait.art',op='npc.plan-portrait-art';
 const source='# 계획 H1\n\n'+planningHeadings[op].map((h,i)=>'## '+h+'\n\n완성 단락 '+i).join('\n\n');
 const content=source.replace('완성 단락 1','확정된 묘사');
 const {binary,read}=await fixture(t,emit({content}));
 const render=createRepresentativeRenderer({codexPath:binary,operation:'npc.planning.update'});
 assert.equal(await render({npcId:input.character.npcId,role,title:'기존 정식 제목',heading:'# 계획 H1',
  requirements:'확정된 묘사만 수정',source,overviewContent:'현재 인물 개요',sources:input.sources}),content);
 const {prompt}=await read();
 assertMandatory(prompt);
 assert.match(prompt,/entire finished Markdown, not a patch/);
 assert.match(prompt,/prioritize visual style, prohibitions, common art and NPC scope/);
 assert.match(prompt,/## 이미지 제작 지시/);
 const packet=JSON.parse(prompt.trimEnd().split('\n').at(-1));
 assert.equal(packet.source,source);
 assert.equal(packet.overviewContent,'현재 인물 개요');
 assert.equal(Object.hasOwn(packet,'representativeContent'),false);
 assert.deepEqual([...new Set(packet.sources.files.map(f=>f.path.split('/')[1]))],['character','expression']);
});

test('NPC custom add CLI only fills externally ordered Markdown section bodies; excludes other plans from prompt',async t=>{
 const bodies={section_1:'첫째',section_2:'둘째'};
 const {binary,read}=await fixture(t,emit(bodies));
 const render=createRepresentativeRenderer({codexPath:binary,operation:'npc.planning.add'});
 const roleInput={npcId:input.character.npcId,categoryId:'category.add.'+'a'.repeat(32),title:'독립 NPC 기획',
  outline:'운영 태도와 습관',requirements:'기존 내용을 바꾸지 않음',requiredSections:['태도','습관'],
  overviewContent:'# 기존 개요',sources:input.sources};
 assert.deepEqual(await render(roleInput),bodies);
 const {schema,prompt}=await read(),data=JSON.parse(prompt.trimEnd().split('\n').at(-1));
 assertMandatory(prompt);
 assert.deepEqual(schema,{type:'object',properties:{section_1:{type:'string'},section_2:{type:'string'}},
  required:['section_1','section_2'],additionalProperties:false});
 assert.match(prompt,/Each field contains only the Markdown body/);
 assert.match(prompt,/do not revise any existing document or generate an image/);
 assert.equal(data.overviewContent,roleInput.overviewContent);
 assert.deepEqual([...new Set(data.sources.files.map(f=>f.path.split('/')[1]))],['character','expression']);
 assert.doesNotMatch(prompt,/이번 프리셋|PRIVATE-FIELD-SOURCES/);
});

for(const operation of Object.keys(planningHeadings))test('CLI boundary combines the registered brief, active Master Concept, and packaged guide for '+operation,async t=>{
 const content='# 제목\n\n'+planningHeadings[operation].map(h=>'## '+h+'\n\n'+(h==='전투 자세'?'무기: 환도, 오른손으로 쥔다.':'기획 본문')).join('\n\n');
 const {binary,read}=await fixture(t,emit({content}));
 const render=createRepresentativeRenderer({codexPath:binary,operation});
 assert.equal(await render({...input,overviewContent:'개요 원본',portraitArtContent:'아트 원본',
  representativeContent:'대표 원본'},{fourDoc:true}),content);
 const {prompt}=await read();
 assertMandatory(prompt);
 for(const heading of planningHeadings[operation])assert(prompt.includes('## '+heading+'\n'));
 const packet=JSON.parse(prompt.trimEnd().split('\n').at(-1));
 assert.equal(packet.intent,input.intent);
 assert.equal(packet.character.role,input.character.role);
 assert.deepEqual([...new Set(packet.sources.files.map(f=>f.path.split('/')[1]))],['character','expression']);
 assert.match(prompt,new RegExp('# '+operation.slice('npc.plan-'.length)+' planning guide'));
 if(operation==='npc.plan-idle')assert.match(prompt,/Remove nonessential handheld items/);
 if(operation==='npc.plan-combat')assert.match(prompt,/A concrete primary weapon is mandatory/);

});
test('a future author supplies purpose and data only and still receives mandatory Markdown and main-item policy',()=>{
 const p=buildAuthoringPrompt({purpose:'Draft a future lane.',data:{outline:'Ordered future main items'}});
 assertMandatory(p);
 assert.match(p,/Return the entire finished Markdown/);
 assert.match(p,/Draft a future lane\./);
});

test('combat output with no concrete weapon cannot be published by the renderer',async t=>{
 const operation='npc.plan-combat';
 for(const weapon of ['','무기: 없음','무기: 미정']){
  const content='# 전투 기획\n\n'+planningHeadings[operation].map(h=>'## '+h+'\n\n기획 본문'+(h==='전투 자세'?'\n'+weapon:'')).join('\n\n');
  const {binary}=await fixture(t,emit({content}));
  await assert.rejects(createRepresentativeRenderer({codexPath:binary,operation})({...input,
   portraitArtContent:'아트 원본',representativeContent:'대표 원본'},{fourDoc:true}),/WORKFLOW_PLAN_COMBAT_WEAPON_REQUIRED/);
 }
});

test('combat-only requirements reach exactly the combat CLI input',async t=>{
 const combatRequirements='전용 예시: 오른손 환도';
 const roleInput={...input,portraitArtContent:'아트 원본',representativeContent:'대표 원본',combatRequirements};
 const operation='npc.plan-combat';
 const content='# 전투 기획\n'+planningHeadings[operation].map(h=>'## '+h+'\n'+(h==='전투 자세'?'무기: 환도, 오른손.':'기획 본문')).join('\n');
 const {binary,read}=await fixture(t,emit({content}));
 assert.equal(await createRepresentativeRenderer({codexPath:binary,operation})(roleInput,{fourDoc:true}),content);
 const {prompt}=await read();
 assert.equal(JSON.parse(prompt.trimEnd().split('\n').at(-1)).combatRequirements,combatRequirements);
 await assert.rejects(createRepresentativeRenderer({codexPath:binary,operation:'npc.plan-idle'})(roleInput,{fourDoc:true}),/WORKFLOW_PLAN_COMBAT_INPUT_INVALID/);
});
