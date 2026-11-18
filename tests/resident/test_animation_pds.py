import copy,hashlib,json,tempfile,unittest
from pathlib import Path
from workers.resident.adapters.pds_animation import AnimationPublicationClient
from workers.resident.adapters.pds_category_files import encode
class PublicationTests(unittest.TestCase):
 def packet(self):
  return {'requestId':'animation.fixture','projectId':'binari','npcId':'npc.fixture','expectedSourceVersion':'version','expectedCharacterHash':'hash','slot':{'posePreset':'idle','presetId':'walk','direction':'s'},'artifact':{'sha256':'a'*64,'bytes':16,'mimeType':'video/mp4','base64':'fixture'},'trace':{}}
 def receipt(self,p):
  return {'requestId':p['requestId'],'inputHash':hashlib.sha256(encode({**p,'artifact':{k:v for k,v in p['artifact'].items()if k!='base64'}})).hexdigest(),'npcId':p['npcId'],'slotKey':'idle/walk/s','assetId':'a'*64+'.mp4','sha256':'a'*64,'bytes':16,'mimeType':'video/mp4','verified':True,'source':'filex','revision':4}
 def test_publish_readback_and_replay_without_second_post(self):
  p=self.packet();r=self.receipt(p);stored={};calls=[]
  client=AnimationPublicationClient('http://127.0.0.1:8030',lambda:{'npcId':'npc.fixture','generationId':'g','requestId':p['requestId'],'leaseToken':'t'},p['requestId'])
  def call(path,packet=None):
   calls.append((path,packet is not None))
   if packet is not None:stored['value']=r
   return stored.get('value')
  client._frame_call=call
  self.assertEqual(client.publish(p),r);self.assertEqual(client.publish(p),r);self.assertEqual(sum(post for _,post in calls),1)
  wrong=copy.deepcopy(p);wrong['expectedSourceVersion']='changed'
  with self.assertRaisesRegex(ValueError,'BINDING'):client.publish(wrong)
 def test_receipt_binding_fields_fail_closed(self):
  client=AnimationPublicationClient('http://127.0.0.1:8030',lambda:{},'animation.fixture');p=self.packet();r=self.receipt(p)
  for key in ('inputHash','npcId','slotKey','assetId','sha256','bytes','verified','source'):
   with self.subTest(key=key),self.assertRaisesRegex(ValueError,'BINDING'):client.verify(p,{**r,key:None})
 def test_unrelated_outputs_do_not_change_input_basis_but_target_and_planning_do(self):
  from workers.resident.adapters.animation_workspace import input_basis,context_basis
  npc={'id':'npc.fixture','creationBasis':{'requestId':'creation'},'planningCategories':[{'content':'canon'}],'animationSlots':{'idle/walk/s':{'assetId':'old'}}}
  request={'posePreset':'idle','presetId':'walk','direction':'s'};before=input_basis(npc,request)
  updated=copy.deepcopy(npc);updated['animationSlots']['idle/walk/n']={'assetId':'other'};updated['animationFrameSets']={'idle/run':{'requestId':'frames'}}
  self.assertEqual(input_basis(updated,request),before)
  updated['animationSlots']['idle/walk/s']={'assetId':'new'};self.assertNotEqual(input_basis(updated,request),before)
  updated=copy.deepcopy(npc);updated['planningCategories'][0]['content']='changed';self.assertNotEqual(input_basis(updated,request),before)
  updated=copy.deepcopy(npc);updated['creationBasis']['requestId']='new';self.assertNotEqual(input_basis(updated,request),before)
  self.assertEqual(context_basis({'npcRevision':1,'sourceVersion':'a','categories':[1]}),context_basis({'npcRevision':2,'sourceVersion':'b','categories':[1]}))
