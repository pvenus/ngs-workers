"""Opt-in actual PDS/Filex fixture integration; never opens a service socket."""
import os,sys,unittest
from pathlib import Path
backend=os.environ.get('NGS_TEST_PDS_SOURCE')
if not backend:raise unittest.SkipTest('NGS_TEST_PDS_SOURCE required')
sys.path[:0]=[backend,str(Path(backend)/'tests')]
from test_npc_animations import Tests as Fixture
from workers.resident.adapters.pds_animation import AnimationPublicationClient
class AnimationPDSIntegration(unittest.TestCase):
 setUp=Fixture.setUp
 def test_real_publisher_receipt_lost_response_replay(self):
  packet=Fixture.packet(self);client=AnimationPublicationClient('http://127.0.0.1:8030',lambda:{'npcId':packet['npcId'],'generationId':'fixture','requestId':packet['requestId'],'leaseToken':'fixture'},packet['requestId']);posts=[]
  def call(path,value=None):
   if value is not None:posts.append(value);return self.api.publish(value)
   return self.api.receipt(packet['requestId'])
  client._frame_call=call;receipt=client.publish(packet);before=self.domain.npcs.current()
  self.assertTrue(receipt['verified']);self.assertEqual(client.publish(packet),receipt);self.assertEqual(len(posts),1);self.assertEqual(before,self.domain.npcs.current())
