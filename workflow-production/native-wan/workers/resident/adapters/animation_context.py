"""Animation input view: appearance is separate from the preset's motion."""
from urllib.parse import urlencode
from .frame_workspace import digest
STYLE_KEYS=('styleBlock','lineHierarchy','palettePolicy','surfaceDetail')

def read_context(npc,request,context_http):
 pose=request['posePreset'];npc_id=npc['id']
 current=context_http('GET','/api/npcs');observed=next(n for n in current['document']['characters']if n['id']==npc_id)
 if observed!=npc:raise ValueError('ANIMATION_SOURCE_CHANGED')
 catalog=context_http('GET','/api/worker-context/catalog?'+urlencode({'projectId':'binari','npcId':npc_id,'workerId':'npc-animation'}))
 if catalog['npcRevision']!=current['revision']or catalog['characterHash']!=digest(npc):raise ValueError('ANIMATION_CONTEXT_VERSION_CHANGED')
 keys=['common.animation-rendering','unique.npc-identity','unique.npc-design']
 context=context_http('POST','/api/worker-context/read',{'projectId':'binari','npcId':npc_id,'npcRevision':current['revision'],'sourceVersion':catalog['sourceVersion'],'categories':keys,'posePreset':pose,'worker':'npc-animation','referenceType':request['referenceType']})
 if context.get('npcRevision')!=current['revision']or context.get('characterHash')!=digest(npc)or context.get('sourceVersion')!=catalog['sourceVersion']:raise ValueError('ANIMATION_CONTEXT_CHANGED')
 if [r['id']for r in context['categories']]!=keys or any(r['contentHash']!=digest(r['value'])for r in context['categories']):raise ValueError('ANIMATION_CONTEXT_HASH_INVALID')
 reference=npc['portraitSets'][pose][request['referenceType']+'AssetId']
 if context.get('trace',{}).get('referenceAssetId')!=reference:raise ValueError('ANIMATION_CONTEXT_REFERENCE_CHANGED')
 prompt_values(context)
 return context

def prompt_values(context,request=None):
 rows={r['id']:r['value']for r in context['categories']}
 style=rows['common.animation-rendering'];planning=rows['unique.npc-design']['planningCategories']
 if style.get('schemaVersion')!='ngs.character-style-contract.v1'or set(style) not in ({'schemaVersion',*STYLE_KEYS},{'schemaVersion',*STYLE_KEYS,'renderingRules'})or any(not isinstance(style[k],str)or not style[k].strip()for k in STYLE_KEYS):raise ValueError('ANIMATION_STYLE_PROJECTION_INVALID')
 if len(planning)!=1 or planning[0].get('planningRole','').startswith('portrait.'):raise ValueError('ANIMATION_STATIC_POSE_BODY_DENIED')
 if not isinstance(planning[0].get('content'),str)or not planning[0]['content'].strip():raise ValueError('ANIMATION_COMMON_ART_REQUIRED')
 extra={}
 if 'renderingRules' in style:
  from .animation_rendering import selected
  extra['applicableRenderingRules']=selected(style['renderingRules'],'animation',(request or{}).get('posePreset'),(request or{}).get('direction'))
 return {**extra,'renderingStyle':{k:style[k]for k in STYLE_KEYS},'identity':rows['unique.npc-identity'],'commonAppearance':planning[0]['content']}
