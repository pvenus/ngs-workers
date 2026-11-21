import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,readFile,realpath,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createHash} from 'node:crypto';
import {preparePlanningSources,readPlanningSources} from './planning-input.mjs';
import {createPlanningFolderDownloader} from './planning-folders.mjs';

// Produced offline from exact PDS as_markdown on fixture section, not production content.
const {section,markdown}=JSON.parse(await readFile(new URL('./master-concept-markdown.fixture.json',import.meta.url),'utf8'));
const documentWorker=(await readFile(new URL('./planning-input.mjs',import.meta.url),'utf8')).includes('includeStatus=false');
const npcId='npc.fixture',concept='binari/concept',planning='binari/npcs/'+npcId+'/planning';
const mdPath='sections/character/content.md';
const hash=value=>createHash('sha256').update(value).digest('hex');
function canonical(value){
 if(Array.isArray(value))return '['+value.map(canonical).join(',')+']';
 if(value!==null&&typeof value==='object')return '{'+Object.keys(value).sort().map(k=>JSON.stringify(k)+':'+canonical(value[k])).join(',')+'}';
 return JSON.stringify(value);
}
const authority=(text=markdown,mode='structured')=>({sectionIds:['character','expression','archived'],_markdownSections:{
 character:{title:section.title,priority:1,mode,sha256:hash(text)},
 archived:{title:'Archived',priority:3,mode:'structured',sha256:'0'.repeat(64)}}});
const base=()=>[
 ['sections/expression/metadata.json',{id:'expression',title:'Expression',summary:'style',fieldIds:['shape']}],
 ['sections/expression/fields/shape.json',{id:'shape',label:'shape',value:'옛 정보'}],
 ['sections/archived/content.md','NOT read, including when indexed'],
];
const legacy=()=>[
 ['sections/character/metadata.json',{id:'character',title:section.title,summary:'old',fieldIds:['identity']}],
 ['sections/character/fields/identity.json',{id:'identity',label:'identity',value:'OLD STALE FIELD'}],
];
function manifest(entries){
 const index=[...entries.map(([path,value])=>[concept,path,value]),[planning,'some.json','{"unrelated":"not included"}']];
 const files=index.map(([scope,path,v])=>{
  const text=typeof v==='string'?v:JSON.stringify(v);
  return {scope,path,sha256:hash(text),size:Buffer.byteLength(text),text};
 }).sort((a,b)=>(a.scope+'/'+a.path)<(b.scope+'/'+b.path)?-1:1);
 return {scopes:[concept,planning],files,manifestSha256:hash(canonical(files.map(({text,...f})=>f)))};
}
const prepare=entries=>preparePlanningSources(manifest(entries),npcId,{includeStatus:true});
const body=result=>result.sources.files.find(f=>f.path===mdPath);
function readValues(result){return JSON.parse(body(result).normalizedText);}
test('exact writer Markdown values, field IDs and summaries bound to one physical SHA; untouched expression fallback',()=>{
 const result=prepare([...base(),...legacy(),['metadata.json',authority()],[mdPath,markdown]]);
 const value=readValues(result);
 assert.equal(body(result).sha256,hash(markdown));
 assert.deepEqual(value,{id:section.id,title:section.title,description:section.description,summary:section.summary,
  fields:section.fields.map(({status,...f})=>documentWorker?{...f,status}:f)});
 assert(!JSON.stringify(result.sources).includes('OLD STALE FIELD'));
 assert(result.sources.files.some(r=>r.path==='sections/expression/fields/shape.json'));
 assert.equal(result.sourceSelection.policy,'representative-filex.v3');
 assert.equal(result.sourceSelection.inventory.find(f=>f.path==='metadata.json').reason,'concept-markdown-authority-index');
 assert.equal(result.sourceSelection.inventory.find(f=>f.path==='sections/character/fields/identity.json').reason,'legacy-markdown-shadow-excluded');
 assert.equal(result.sourceSelection.inventory.find(f=>f.path==='sections/archived/content.md').selected,false);
 assert.equal(result.sources.manifestSha256,manifest([...base(),...legacy(),['metadata.json',authority()],[mdPath,markdown]]).manifestSha256);
});
test('unmarked Markdown never overrides healthy old fields or substitutes for missing legacy authority',()=>{
 const result=prepare([...base(),...legacy(),['metadata.json',{sectionIds:['character','expression']}],[mdPath,markdown]]);
 assert.equal(body(result),undefined);
 assert.equal(result.sourceSelection.policy,'representative-filex.v2');
 assert(result.sources.files.some(r=>r.normalizedText.includes('OLD STALE FIELD')));
 assert.throws(()=>prepare([...base(),[mdPath,markdown]]),/CONCEPT_REQUIRED/);
});
test('marked missing, wrong physical digest and wrong root index fail closed even if old JSON coexists',()=>{
 const old=[...base(),...legacy()];
 assert.throws(()=>prepare([...old,['metadata.json',authority()]]),/MARKDOWN_CHANGED/);
 assert.throws(()=>prepare([...old,['metadata.json',authority()],[mdPath,markdown+'drift']]),/MARKDOWN_CHANGED/);
 const wrong=authority();wrong._markdownSections.character.priority=2;
 assert.throws(()=>prepare([...old,['metadata.json',wrong],[mdPath,markdown]]),/MARKDOWN_INDEX/);
});
test('valid hash cannot excuse mismatched section, heading, reordered fields or unclosed fence',()=>{
 const old=[...base(),...legacy()];
 for(const bad of [markdown.replace('"id":"character"','"id":"expression"'),
  markdown.replace('# '+section.title,'# Wrong title'),
  markdown.replace('["identity","new.active"]','["new.active","identity"]'),
  markdown.replace('## 정체성 규칙','## Wrong label'),
  markdown.slice(0,markdown.lastIndexOf(String.fromCharCode(96).repeat(3)))]){
  assert.throws(()=>prepare([...old,['metadata.json',authority(bad)],[mdPath,bad]]),/CONCEPT_IDENTITY|MARKDOWN_INVALID/);
 }
});
test('authored Markdown stays literal, with the writer projection status and no synthetic regeneration',()=>{
 const text='# '+section.title+'\r\n\r\n## 안내\r\n\r\n**내용**\r\n';
 const result=prepare([...base(),['metadata.json',authority(text,'authored')],[mdPath,text]]);
 const v=readValues(result);
 assert.equal(v.fields[0].value,text);
 assert.equal(v.fields[0].id,'content');
 assert.deepEqual(v.summary,{sentence:section.title,keywords:['마스터 컨셉']});
 if(documentWorker)assert.equal(v.fields[0].status,'proposal');
});
test('actual folder downloader + reader uses its existing two scope compares/chunks only',async t=>{
 const tempParent=await realpath(await mkdtemp(join(tmpdir(),'workflow-md-reader-')));
 t.after(()=>rm(tempParent,{recursive:true,force:true}));
 const prepared=manifest([...base(),['metadata.json',authority()],[mdPath,markdown]]);
 const calls=[];
 const call=async (name,args)=>{
  calls.push({name,...args});
  const files=prepared.files.filter(r=>r.scope===args.scope);
  if(name==='npc_folders_compare'){
   const groups=Map.groupBy(files,r=>r.path.includes('/')?r.path.split('/')[0]:'');
   return {scope:args.scope,groups:[...groups].map(([folder,files])=>({folder,files:files.map(({path,sha256,size})=>({path,sha256,size}))}))};
  }
  assert.equal(name,'npc_folder_file_chunk');
  const r=files.find(f=>f.path===args.path);assert.equal(r.sha256,args.sha256);
  return {scope:r.scope,path:r.path,sha256:r.sha256,size:r.size,offset:0,nextOffset:null,base64:Buffer.from(r.text).toString('base64')};
 };
 const selection=await createPlanningFolderDownloader({call,tempParent})({npcId},async snapshot=>
  preparePlanningSources(await readPlanningSources(snapshot,npcId),npcId));
 assert.equal(readValues(selection).fields[0].value,section.fields[0].value);
 assert.equal(calls.filter(c=>c.name==='npc_folders_compare').length,4);
 assert.equal(calls.filter(c=>c.name==='npc_folder_file_chunk').length,prepared.files.length);
 assert(!calls.some(c=>c.path?.endsWith('sections/character/fields/identity.json')));
});
