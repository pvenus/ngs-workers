import os,sys,sqlite3,unittest
from unittest.mock import patch
from workers.resident.adapters.music_intake import MusicIntake,TYPE
@unittest.skipUnless(os.environ.get('NGS_RUNTIME_SOURCE'),'explicit isolated source required')
class Tests(unittest.TestCase):
 def setUp(self):
  sys.path.insert(0,os.environ['NGS_RUNTIME_SOURCE']);self.addCleanup(lambda:sys.path.remove(os.environ['NGS_RUNTIME_SOURCE']))
  from local.services.request_pipeline import common
  self.module=common;self.db=sqlite3.connect(':memory:',isolation_level=None);self.addCleanup(self.db.close)
  self.common=common.CommonRequests(db=self.db);self.port=MusicIntake(self.common);self.port.initialize_schema()
  self.request={'prompt':'fixture music','model':'acestep-xl-sft-lm4b','kind':'music','duration_seconds':10,'seed':42}
 def test_unsupported_deployment_type_rolls_back(self):
  with patch.object(self.module,'KINDS',self.module.KINDS-{TYPE}):
   with self.assertRaisesRegex(ValueError,'TYPE_UNSUPPORTED'):self.port.accept('actor','key','project','target','1',self.request)
  self.assertEqual(self.db.execute('select count(*) from resident_music_payloads').fetchone()[0],0)
 def test_formal_original_dto_idempotency_and_scope(self):
  with patch.object(self.module,'KINDS',self.module.KINDS|{TYPE}):
   result=self.port.accept('actor','key','project','target','1',self.request)
   self.assertEqual(result,self.port.accept('actor','key','project','target','1',self.request))
   rid=self.db.execute('select request_id from resident_music_payloads').fetchone()[0]
   item=self.port.payload(rid);self.assertEqual(item['parameters'],self.request);self.assertEqual(item['binding']['targetId'],'target')
   with self.assertRaisesRegex(ValueError,'IDEMPOTENCY_CONFLICT'):self.port.accept('actor','key','project','other','1',self.request)
  self.assertEqual(self.db.execute('select count(*) from common_requests').fetchone()[0],1)
