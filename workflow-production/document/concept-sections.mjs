/** A required add-section title is request-owned; the renderer only fills its body. */
export const sectionBodyKeys=count=>Array.from({length:count},(_,i)=>'section_'+(i+1));

export function validateSectionBodies(bodies,count){
 const keys=sectionBodyKeys(count);
 if(!bodies||typeof bodies!=='object'||Array.isArray(bodies)||Object.keys(bodies).length!==keys.length||
  !keys.every(k=>Object.hasOwn(bodies,k)&&typeof bodies[k]==='string'&&bodies[k].isWellFormed()&&bodies[k].trim()&&
   bodies[k].length<=12000&&!/[\u0000-\u0008\u000B-\u000C\u000E-\u001F\u007F]/u.test(bodies[k])))
  throw Error('WORKFLOW_CONCEPT_SECTION_BODIES_INVALID');
 return keys.map(k=>bodies[k]);
}

const literal=value=>value.replace(/[\\`*_{}\[\]()#+\-.!|>~<&=]/gu,'\\$&');
// Preserve body Markdown inline styles and lists; only neutralize constructs
// that could add headings or engulf the next externally assigned heading.
const bodyMarkdown=value=>value.replaceAll('\r\n','\n').trim()
 .replace(/<(?=!--|\/?[a-z][a-z0-9-]*(?:[\s/>]|$)|[!?])/giu,'\\<')
 .replace(/^( {0,3}(?:>[ \t]? {0,3})*)(?=(?:#{1,6}(?:[ \t]|$)|`{3,}|~{3,}|(?:=+|-+)[ \t]*$))/gmu,'$1\\');
export function formatConceptSections(title,sections,bodies){
 const ordered=validateSectionBodies(bodies,sections.length);
 const content='# '+title+'\n\n'+sections.map((label,i)=>
  '## '+literal(label)+'\n\n'+bodyMarkdown(ordered[i])).join('\n\n')+'\n';
 if(content.length>12000)throw Error('WORKFLOW_CONCEPT_SECTION_BODIES_TOO_LARGE');
 return content;
}
