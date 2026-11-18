import json,tempfile,unittest
import unittest.mock
from pathlib import Path
from videoforge_mcp.contracts import candidate_indices,selection_manifest
from videoforge_mcp.queue import JobQueue
class ContractTests(unittest.TestCase):
 def test_candidate_boundaries(self):
  for frames,expected in ((1,1),(2,2),(59,59),(60,60),(120,60)):
   got=candidate_indices(frames,30);self.assertEqual(len(got),expected);self.assertEqual(got[0],0);self.assertEqual(got[-1],frames-1);self.assertEqual(len(got),len(set(got)))
 def test_selection_is_manifest_bound_and_not_automatic(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);m=p/'c.json';m.write_text(json.dumps({'candidates':[{'candidate_id':'a'},{'candidate_id':'b'}]}))
   result=selection_manifest(m,['b'],p/'s.json',p,p);self.assertFalse(result['automatic_final_selection']);self.assertEqual(result['selected_candidate_ids'],['b'])
   with self.assertRaises(ValueError):selection_manifest(m,['x'],p/'bad.json',p,p)
class QueueTests(unittest.TestCase):
 def test_consent_idempotency_fifo_and_cancel(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);(p/'input.png').write_bytes(b'image');q=JobQueue(p/'jobs',p);req={'idempotency_key':'x','profile_id':'p','input_image':{'consent':True,'rights_attested':True,'path':str(p/'input.png')},'timeout_seconds':10}
   with unittest.mock.patch('videoforge_mcp.queue.validate_capacity'):
    a=q.submit(req);self.assertEqual(q.submit(req)['job_id'],a['job_id'])
    b=q.submit({**req,'idempotency_key':'y'});job,lock=q.claim_one();self.assertEqual(job['job_id'],a['job_id']);self.assertIsNone(q.claim_one()[0]);lock.close();self.assertEqual(q.cancel(b['job_id'])['status'],'cancelled')
 def test_rights_gate(self):
  with tempfile.TemporaryDirectory() as d:
   with self.assertRaises(ValueError):JobQueue(Path(d)).submit({'idempotency_key':'x','profile_id':'p','input_image':{'consent':False}})

if __name__=='__main__':unittest.main()
