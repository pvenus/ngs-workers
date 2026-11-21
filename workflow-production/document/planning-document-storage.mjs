import {createHash} from 'node:crypto';
const categoryIdPattern=/^category\.preset\.[a-f0-9]{32}$/u;
const documentSha=value=>createHash('sha256').update(value,'utf8').digest('hex');
const safe=(condition)=>{if(!condition)throw Error('WORKFLOW_PLAN_STORAGE_BINDING');};

export function resolvePlanningDocument(sources,categoryId){
 safe(typeof categoryId==='string'&&categoryIdPattern.test(categoryId));
 const scope=sources.scopes[1];
 const rows=sources.files.filter(f=>f.scope===scope&&f.path===categoryId+'.json');
 if(!rows.length)return null;
 safe(rows.length===1);
 let doc;
 try{doc=JSON.parse(rows[0].text);}catch{throw Error('WORKFLOW_PLAN_STORAGE_BINDING');}
 safe(doc&&typeof doc==='object'&&!Array.isArray(doc)&&doc.id===categoryId);
 const hasFormat=Object.hasOwn(doc,'contentFormat'),hasHash=Object.hasOwn(doc,'contentSha256');
 if(!hasFormat&&!hasHash){
  const bodyPath=categoryId+'/content.md';
  safe(typeof doc.content==='string'&&!sources.files.some(f=>f.scope===scope&&f.path===bodyPath));
  return {...doc,sourceSha256:documentSha(doc.content)};
 }
 safe(hasFormat&&hasHash&&doc.contentFormat==='markdown.v1'&&typeof doc.contentSha256==='string'&&
  /^[a-f0-9]{64}$/u.test(doc.contentSha256)&&!Object.hasOwn(doc,'content'));
 const body=sources.files.filter(f=>f.scope===scope&&f.path===categoryId+'/content.md');
 safe(body.length===1&&typeof body[0].text==='string'&&body[0].sha256===doc.contentSha256&&
  documentSha(body[0].text)===doc.contentSha256);
 return {...doc,content:body[0].text,sourceSha256:doc.contentSha256};
}

/** A role-only read is exact and unique within the downloaded, verified NPC scope. */
export function resolvePlanningRole(sources,role,{optional=false}={}){
 const scope=sources.scopes[1];
 const match=sources.files.filter(f=>f.scope===scope&&f.path.endsWith('.json')&&categoryIdPattern.test(f.path.slice(0,-5)))
  .filter(f=>{try{return JSON.parse(f.text)?.planningRole===role;}catch{throw Error('WORKFLOW_PLAN_STORAGE_BINDING');}});
 if(optional&&match.length===0)return null;
 safe(match.length===1);
 return resolvePlanningDocument(sources,match[0].path.slice(0,-5));
}
