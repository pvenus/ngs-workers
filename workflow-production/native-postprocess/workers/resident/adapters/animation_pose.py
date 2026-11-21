"""Current, direction-matched pose assets. No canonical/rear-generation fallback."""
import re

from .frame_workspace import digest
POLICY='ngs.animation-pose-view-reference.v1'
def binding(npc,request):
 pose=request['posePreset'];kind=request['referenceType'];direction=request['direction']
 source=npc.get('portraitSets',{}).get(pose,{}).get(kind+'AssetId')
 view=npc.get('poseViews',{}).get(pose,{}).get(kind,{}).get(direction)
 if not isinstance(view,dict):raise ValueError('ANIMATION_DIRECTION_POSE_REQUIRED')
 if view.get('schemaVersion')!='ngs.npc-pose-view.v1' or any(view.get(k)!=v for k,v in [('posePreset',pose),('referenceType',kind),('direction',direction)]):raise ValueError('ANIMATION_DIRECTION_POSE_INVALID')
 if view.get('sourceAssetId')!=source or view.get('sourceHash')!=str(source).split('.')[0]:raise ValueError('ANIMATION_DIRECTION_POSE_STALE')
 asset=view.get('assetId')
 if not isinstance(asset,str)or not re.fullmatch(r'[a-f0-9]{64}\.png',asset):raise ValueError('ANIMATION_DIRECTION_POSE_HASH_INVALID')
 if kind=='pixel' and (view.get('width'),view.get('height'))!=(384,384):raise ValueError('ANIMATION_DIRECTION_POSE_DIMENSIONS')
 return {'policy':POLICY,'posePreset':pose,'referenceType':kind,'direction':direction,'sourceAssetId':source,'referenceAssetId':asset,'poseViewHash':digest(view)}
