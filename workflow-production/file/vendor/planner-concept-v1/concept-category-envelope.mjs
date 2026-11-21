/** Registration metadata for one exact concept category; never a file action. */
export const conceptCategoryRequestType='concept.category.workflow';
export const conceptCategorySchema='ngs.concept-category-workflow.v1';
export const conceptCategoryIdPattern='^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$';
export const conceptCategoryGroupIdPattern='^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$';
export const conceptCategoryAddIdPattern='^category\\.add\\.[a-f0-9]{32}$';
const conceptCategoryId=new RegExp(conceptCategoryIdPattern);
const conceptCategoryGroupId=new RegExp(conceptCategoryGroupIdPattern);
const conceptCategoryAddId=new RegExp(conceptCategoryAddIdPattern);
const fields=['schemaVersion','projectId','operation','categoryId','groupId','title','outline','requirements'];
const fits=(text,min,max)=>{
 if(typeof text!=='string'||!text.isWellFormed()||text!==text.trim()||/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/u.test(text))return false;
 const length=[...text].length;
 return length>=min&&length<=max;
};
export function validateConceptCategory(value,projectId){
 if(!value||typeof value!=='object'||Array.isArray(value)||Object.keys(value).length!==fields.length||
  !fields.every(k=>Object.hasOwn(value,k))||projectId!=='binari'||value.projectId!==projectId||
  value.schemaVersion!==conceptCategorySchema||!['add','replace','migrate','remove'].includes(value.operation)||
  typeof value.categoryId!=='string'||value.categoryId!==value.categoryId.trim()||
  !(value.operation==='add'?conceptCategoryAddId:conceptCategoryId).test(value.categoryId)||
  (value.operation==='add'?(typeof value.groupId!=='string'||value.groupId!==value.groupId.trim()||!conceptCategoryGroupId.test(value.groupId)):
   value.groupId!==null)||
  !fits(value.title,1,120)||/[\t\r\n\u2028\u2029]/u.test(value.title)||
  (['add','replace'].includes(value.operation)?(!fits(value.outline,1,4000)||!fits(value.requirements,0,4000)):
   value.outline!==''||value.requirements!==''))
  throw Error('NGS_CONCEPT_CATEGORY_PAYLOAD_INVALID');
 // All validated fields are scalar. Sorted-key canonical JSON is hashed on
 // registration and validated again before workflow handoff.
 return JSON.stringify(Object.fromEntries([...fields].sort().map(k=>[k,value[k]])));
}
