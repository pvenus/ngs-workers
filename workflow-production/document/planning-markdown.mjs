/** Fixed authored Markdown output structure; never executes or follows source text. */
const headings={
 'npc.plan-overview':['인물과 역할','관계와 서사','현재 상태','확정과 미정'],
 'npc.plan-portrait-art':['이미지 제작 지시','인물 외형','복장과 색','화풍과 표현 금지','확정과 제안'],
 'npc.plan-representative':['포즈 제작 지시','대표 자세와 표정','카메라와 구도','검수 기준'],
 'npc.plan-idle':['포즈 제작 지시','유지할 외형','정지 자세','방향별 가림','검수 기준'],
 'npc.plan-combat':['포즈 제작 지시','유지할 외형','전투 자세','방향별 가림','검수 기준'],
};
export const planningHeadings=Object.freeze(Object.fromEntries(
 Object.entries(headings).map(([operation,sections])=>[operation,Object.freeze(sections)])));
const reject=()=>{throw Error('WORKFLOW_PLAN_MARKDOWN_CONTRACT');};

export function validatePlanningMarkdown(content,operation){
 const expected=Object.hasOwn(planningHeadings,operation)?planningHeadings[operation]:null;
 if(!expected||typeof content!=='string'||!content.isWellFormed()||
    !content.trim()||[...content].length>16000||/[\u0000-\u0008\u000B-\u000C\u000E-\u001F\u007F]/u.test(content))reject();
 const lines=content.trim().replaceAll('\r\n','\n').split('\n');
 if(!/^# [^#\s].*$/u.test(lines.shift()??''))reject();
 const chunks=[];
 for(const line of lines){
  if(/^ {0,3}(?:```|~~~|<\s*\/?\s*h[1-6]\b)/iu.test(line)||/^ {0,3}(?:={3,}|-{3,})\s*$/u.test(line))reject();
  if(/^ {0,3}#{1,6}(?:\s|$)/u.test(line)){
   const heading=expected[chunks.length];
   if(line!=='## '+heading||heading===undefined)reject();
   chunks.push([]);
  }else if(chunks.length)chunks.at(-1).push(line);
  else if(line.trim())reject();
 }
 if(chunks.length!==expected.length)reject();
 const values=chunks.map(lines=>lines.join('\n').trim());
 if(values.some(value=>!/[\p{L}\p{N}]/u.test(value)))reject();
 if(operation!=='npc.plan-overview'&&([...values[0]].length>1000||
   /(?:https?:\/\/|file:\/\/|canonical-files\/|sections\/[^\s]+\.json|\b[a-f0-9]{64}\b)/iu.test(values[0])))reject();
 return Object.freeze(Object.fromEntries(expected.map((h,i)=>[h,values[i]])));
}
