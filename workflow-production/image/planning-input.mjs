import {constants} from 'node:fs';
import {lstat,open,realpath} from 'node:fs/promises';
import {isAbsolute,join} from 'node:path';
import {readMasterMarkdown} from './master-concept-markdown.mjs';
import {canonical,sha} from './vendor/planner-f24d23a/envelope.mjs';
import {exact} from './vendor/planner-f24d23a/planner.mjs';

const sort=(a,b)=>a<b?-1:a>b?1:0;
const media=/\.(?:png|jpe?g|gif|webp|bmp|avif|heic|ico|tiff?|svg|mp4|mov|avi|wav|mp3|flac|webm|zip|pdf)$/i;
const cleanPath=path=>typeof path==='string'&&path.isWellFormed()&&!path.includes('\\')&&!/[\u0000-\u001F\u007F]/u.test(path)&&
 path.split('/').every(p=>p&&p!=='.'&&p!=='..')&&!media.test(path);
const shaHex=(value)=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
function safe(v,code){if(!v)throw Error(code);}

export function validatePlanningSources(sources,npcId){
 const scopes=['binari/concept','binari/npcs/'+npcId+'/planning'];
 safe(exact(sources,['scopes','files','manifestSha256'])&&canonical(sources.scopes)===canonical(scopes)&&
  Array.isArray(sources.files)&&sources.files.length>0&&shaHex(sources.manifestSha256),'WORKFLOW_PLAN_SOURCE_SHAPE');
 const key=(f)=>f.scope+'/'+f.path;
 const seen=new Set(), present=new Set();
 let last;
 for(const f of sources.files){
  safe(exact(f,['scope','path','sha256','size','text'])&&scopes.includes(f.scope)&&cleanPath(f.path)&&shaHex(f.sha256)&&
    Number.isSafeInteger(f.size)&&f.size>=0&&typeof f.text==='string'&&f.text.isWellFormed()&&
    !/[\u0000-\u0008\u000B-\u000C\u000E-\u001F\u007F]/u.test(f.text)&&Buffer.byteLength(f.text)===f.size&&sha(f.text)===f.sha256,
    'WORKFLOW_PLAN_SOURCE_BINDING');
  safe(!seen.has(key(f))&&(last===undefined||sort(last,key(f))<0),'WORKFLOW_PLAN_SOURCE_ORDER');
  present.add(f.scope);seen.add(key(f));last=key(f);
 }
 safe(present.size===scopes.length,'WORKFLOW_PLAN_SOURCE_SCOPE_EMPTY');
 const projection=sources.files.map(({text,...binding})=>binding);
 safe(sha(canonical(projection))===sources.manifestSha256,'WORKFLOW_PLAN_MANIFEST_CHANGED');
 return sources;
}

export async function readPlanningSources(snapshot,npcId){
 const scopes=['binari/concept','binari/npcs/'+npcId+'/planning'];
 safe(snapshot&&Array.isArray(snapshot.files)&&typeof snapshot.root==='string'&&isAbsolute(snapshot.root)&&
  await realpath(snapshot.root)===snapshot.root,'WORKFLOW_PLAN_SNAPSHOT_REQUIRED');
 const root=await lstat(snapshot.root);
 safe(root.isDirectory()&&root.uid===process.getuid()&&!(root.mode&0o077),'WORKFLOW_PLAN_SNAPSHOT_UNTRUSTED');
 const files=[];
 for(const row of snapshot.files){
  safe(exact(row,['scope','path','sha256','size','file'])&&scopes.includes(row.scope)&&cleanPath(row.path)&&
   shaHex(row.sha256)&&Number.isSafeInteger(row.size)&&row.size>=0&&typeof row.file==='string'&&
   row.file===join(snapshot.root,row.scope===scopes[0]?'concept':'planning',row.path)&&
   await realpath(row.file)===row.file,'WORKFLOW_PLAN_SNAPSHOT_FILE_INVALID');
  const handle=await open(row.file,constants.O_RDONLY|constants.O_NOFOLLOW|constants.O_NONBLOCK);
  try{
   const st=await handle.stat();
   safe(st.isFile()&&st.uid===process.getuid()&&!(st.mode&0o077)&&st.nlink===1&&st.size===row.size,'WORKFLOW_PLAN_SNAPSHOT_FILE_UNTRUSTED');
   const bytes=await handle.readFile();
   const text=new TextDecoder('utf-8',{fatal:true,ignoreBOM:true}).decode(bytes);
   const {file,...binding}=row;
   files.push({...binding,text});
  }finally{await handle.close();}
 }
 files.sort((a,b)=>sort(a.scope+'/'+a.path,b.scope+'/'+b.path));
 return validatePlanningSources({scopes,files,manifestSha256:sha(canonical(files.map(({text,...binding})=>binding)))},npcId);
}


// The current Filex section index is the source of active field IDs.
const sections=['character','expression'];
const fieldId=/^[A-Za-z0-9][A-Za-z0-9._-]*$/u;
const nonempty=x=>typeof x==='string'&&x.trim().length>0;
const object=x=>x!==null&&typeof x==='object'&&!Array.isArray(x);
function readJson(row){
 let doc;
 try{doc=JSON.parse(row.text.replace(/^\uFEFF/u,''));}catch{throw Error('WORKFLOW_PLAN_SOURCE_JSON_INVALID');}
 return doc;
}
function summaryOf(summary){
 if(typeof summary==='string')return summary;
 safe(object(summary)&&typeof summary.sentence==='string'&&Array.isArray(summary.keywords)&&
  summary.keywords.every(word=>typeof word==='string'),'WORKFLOW_PLAN_CONCEPT_SUMMARY');
 return {sentence:summary.sentence,keywords:summary.keywords};
}

export function preparePlanningSources(sources,npcId){
 const trace=validatePlanningSources(sources,npcId),files=[],inventory=[];
 const current=new Map(trace.files.filter(row=>row.scope===trace.scopes[0]).map(row=>[row.path,row]));
 const selected=new Map(),marked=new Set();
 const root=current.has('metadata.json')?readJson(current.get('metadata.json')):null;
 for(const id of sections){
  const directory=`sections/${id}`,path=directory+'/metadata.json',markdownPath=directory+'/content.md';
  const body=readMasterMarkdown(root,id,current.get(markdownPath));
  if(body){
   const {doc,fields}=body;
   safe(!Object.hasOwn(doc,'description')||typeof doc.description==='string','WORKFLOW_PLAN_CONCEPT_SECTION');
   selected.set(markdownPath,{kind:'concept-markdown-section',data:{id,title:doc.title,description:doc.description,
    summary:summaryOf(doc.summary),fields:fields.map(f=>({id:f.id,label:f.label,value:f.value,
     summary:Object.hasOwn(f,'summary')?summaryOf(f.summary):undefined}))}});
   marked.add(id);
   continue;
  }
  safe(current.has(path),'WORKFLOW_PLAN_CONCEPT_REQUIRED');
  const doc=readJson(current.get(path));
  safe(object(doc)&&doc.id===id,'WORKFLOW_PLAN_CONCEPT_IDENTITY');
  safe(nonempty(doc.title)&&Array.isArray(doc.fieldIds)&&doc.fieldIds.every(fid=>typeof fid==='string'&&fieldId.test(fid))&&
   new Set(doc.fieldIds).size===doc.fieldIds.length,'WORKFLOW_PLAN_CONCEPT_SECTION');
  safe(!Object.hasOwn(doc,'description')||typeof doc.description==='string','WORKFLOW_PLAN_CONCEPT_SECTION');
  selected.set(path,{kind:'concept-section',data:{id,title:doc.title,description:doc.description,summary:summaryOf(doc.summary)}});
  for(const fid of doc.fieldIds){
   const fieldPath=`${directory}/fields/${fid}.json`;
   safe(current.has(fieldPath),'WORKFLOW_PLAN_CONCEPT_REQUIRED');
   const field=readJson(current.get(fieldPath));
   safe(object(field)&&field.id===fid,'WORKFLOW_PLAN_CONCEPT_IDENTITY');
   safe(nonempty(field.label)&&typeof field.value==='string','WORKFLOW_PLAN_CONCEPT_FIELD');
   selected.set(fieldPath,{kind:'concept-field',data:{id:fid,label:field.label,value:field.value,
    summary:Object.hasOwn(field,'summary')?summaryOf(field.summary):undefined}});
  }
 }
 for(const {text,...binding} of trace.files){
  const inConcept=binding.scope===trace.scopes[0],entry=inConcept?selected.get(binding.path):undefined;
  const reason=entry?.kind??(!inConcept?'npc-planning-excluded':
   binding.path==='metadata.json'&&marked.size?'concept-markdown-authority-index':
   [...marked].some(id=>binding.path.startsWith(`sections/${id}/`))?'legacy-markdown-shadow-excluded':
   sections.some(id=>binding.path.startsWith(`sections/${id}/`))?
   'not-in-section-index':'other-concept-section');
  inventory.push({...binding,selected:Boolean(entry),reason});
  if(entry)files.push({...binding,format:'json',normalizedText:JSON.stringify(entry.data)});
 }
 return {
  sources:{scopes:[trace.scopes[0]],manifestSha256:trace.manifestSha256,files},
  sourceSelection:{policy:marked.size?'representative-filex.v3':'representative-filex.v2',manifestSha256:trace.manifestSha256,inventory},
 };
}
