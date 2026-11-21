"""Explicitly scoped master rendering rules. No inference from natural-language prose."""
import json
SCHEMA='ngs.character-rendering-rules.v1'
DIRECTIONS={'n','ne','e','se','s','sw','w','nw'}
def parse(value):
 if isinstance(value,str):
  try:value=json.loads(value)
  except ValueError:raise ValueError('MASTER_RENDERING_RULES_JSON')
 if not isinstance(value,dict) or set(value)!={'schemaVersion','rules'} or value['schemaVersion']!=SCHEMA or not isinstance(value['rules'],list) or len(value['rules'])>64:raise ValueError('MASTER_RENDERING_RULES_SCHEMA')
 seen=set()
 for rule in value['rules']:
  if not isinstance(rule,dict) or set(rule)!={'id','stages','posePresets','directions','instruction'}:raise ValueError('MASTER_RENDERING_RULE_SCHEMA')
  if not isinstance(rule['id'],str) or not rule['id'].strip() or rule['id'] in seen:raise ValueError('MASTER_RENDERING_RULE_ID')
  seen.add(rule['id'])
  if not isinstance(rule['instruction'],str) or not rule['instruction'].strip() or len(rule['instruction'])>3000:raise ValueError('MASTER_RENDERING_RULE_TEXT')
  for key,allowed in [('stages',{'portrait','animation'}),('posePresets',{'representative','idle','combat'}),('directions',DIRECTIONS)]:
   xs=rule[key]
   if not isinstance(xs,list) or not xs or any(not isinstance(x,str) or x not in allowed for x in xs) or len(set(xs))!=len(xs):raise ValueError('MASTER_RENDERING_RULE_SCOPE')
 return value

def registered(field):
 try:return json.loads(field.get('value','')).get('schemaVersion')==SCHEMA
 except (ValueError,TypeError,AttributeError):return False

def selected(value,stage,pose=None,direction=None):
 rules=parse(value)['rules']
 if stage not in ('portrait','animation') or pose is not None and pose not in ('representative','idle','combat') or direction is not None and direction not in DIRECTIONS:raise ValueError('MASTER_RENDERING_RULE_SELECTION')
 return [dict(r) for r in rules if stage in r['stages'] and (pose is None or pose in r['posePresets']) and (direction is None or direction in r['directions'])]

def fields(concept):
 hierarchy=concept['document'].get('hierarchy',{})
 rows=[f for s in concept['document']['sections'] if s['id']=='expression' and s.get('lifecycleState')!='deleted' and s['id'] not in hierarchy.get('archivedCategoryIds',[]) for f in s['fields'] if f.get('lifecycleState')!='deleted' and {'categoryId':s['id'],'itemId':f['id']} not in hierarchy.get('archivedItems',[]) and registered(f)]
 if len(rows)>1:raise ValueError('MASTER_RENDERING_RULES_AMBIGUOUS')
 for f in rows:parse(f['value'])
 return rows
