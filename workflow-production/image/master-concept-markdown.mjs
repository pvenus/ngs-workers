// Filex physical master-concept body. Only a hash-bound root authority marker
// activates Markdown. Legacy JSON remains valid only for unmarked sections.
const maxBody=1024*1024;
const fieldId=/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}(?![\s\S])/u;
const sha256=/^[a-f0-9]{64}$/u;
const object=v=>v!==null&&typeof v==='object'&&!Array.isArray(v);
const nonempty=v=>typeof v==='string'&&v.trim().length>0;
const exact=(v,keys)=>object(v)&&Object.keys(v).length===keys.length&&keys.every(k=>Object.hasOwn(v,k));
const assert=(v,code='WORKFLOW_PLAN_CONCEPT_MARKDOWN_INVALID')=>{if(!v)throw Error(code);};
export const masterMarkdownIndexKey='_markdownSections';

function parseMeta(line,kind){
 const prefix='<!-- ngs/'+kind+':',suffix=' -->';
 assert(typeof line==='string'&&line.startsWith(prefix+'{')&&line.endsWith('}'+suffix));
 try{
  const meta=JSON.parse(line.slice(prefix.length,-suffix.length));
  assert(object(meta));return meta;
 }catch{throw Error('WORKFLOW_PLAN_CONCEPT_MARKDOWN_INVALID');}
}

function heading(value){
 const lines=value.split(/\r\n|[\r\n\u000B\u000C\u001C-\u001E\u0085\u2028\u2029]/u);
 if(lines.at(-1)==='')lines.pop(); // Same terminal-line behavior as Python splitlines().
 return lines.join(' ');
}

function parseStructured(text,id,title){
 const lines=text.split('\n'),doc=parseMeta(lines[0],'category');
 assert(doc.id===id&&doc.title===title,'WORKFLOW_PLAN_CONCEPT_IDENTITY');
 assert(lines[1]==='# '+heading(title));
 assert(Array.isArray(doc.fieldIds)&&doc.fieldIds.every(v=>typeof v==='string'&&fieldId.test(v))&&
  new Set(doc.fieldIds).size===doc.fieldIds.length);
 const fields=[];
 for(let i=1;i<lines.length;i++){
  if(!lines[i].startsWith('<!-- ngs/'))continue;
  const field=parseMeta(lines[i],'field');
  assert(field.id===doc.fieldIds[fields.length]&&nonempty(field.label)&&!Object.hasOwn(field,'value'));
  i++;
  assert(lines[i]==='## '+heading(field.label));i++;
  const opening=/^(`{3,})text$/u.exec(lines[i]??'');
  assert(opening);
  const stop=lines.indexOf(opening[1],i+1);
  assert(stop>i+1&&stop<lines.length-1);
  field.value=lines.slice(i+1,stop).join('\n');
  fields.push(field);i=stop;
 }
 assert(fields.length===doc.fieldIds.length);
 return {doc,fields};
}

export function readMasterMarkdown(root,id,row){
 const index=root?.[masterMarkdownIndexKey];
 if(!root||!Object.hasOwn(root,masterMarkdownIndexKey))return null;
 assert(object(index),'WORKFLOW_PLAN_CONCEPT_MARKDOWN_INDEX');
 if(!Object.hasOwn(index,id))return null;
 const binding=index[id];
 assert(exact(binding,['title','priority','mode','sha256'])&&nonempty(binding.title)&&
  Number.isSafeInteger(binding.priority)&&binding.priority>0&&typeof binding.sha256==='string'&&sha256.test(binding.sha256)&&
  ['structured','authored'].includes(binding.mode)&&Array.isArray(root.sectionIds)&&root.sectionIds[binding.priority-1]===id,
  'WORKFLOW_PLAN_CONCEPT_MARKDOWN_INDEX');
 assert(row&&row.path==='sections/'+id+'/content.md'&&row.sha256===binding.sha256&&
  row.size>0&&row.size<=maxBody&&nonempty(row.text),'WORKFLOW_PLAN_CONCEPT_MARKDOWN_CHANGED');
 if(binding.mode==='structured')return parseStructured(row.text,id,binding.title);
 // Match the authored category projection (the exact Markdown, not a regenerated summary).
 const summary={sentence:binding.title,keywords:['마스터 컨셉']};
 assert(row.text.split(/\r\n|[\r\n]/u,1)[0]==='# '+binding.title);
 return {doc:{id,title:binding.title,description:'',summary},
  fields:[{id:'content',label:'내용',value:row.text,status:'proposal',summary}]};
}
