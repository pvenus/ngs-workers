import copy,json,math,struct,tempfile,unittest,wave
from pathlib import Path
from fastapi.testclient import TestClient
from audioforge.review_data import measure_audio,validate_score,atomic_json
from audioforge.review_api import create_review_app

class ReviewTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.art=self.root/'artifacts';self.aid='a'*32;folder=self.art/self.aid;folder.mkdir(parents=True)
  self.audio=folder/'audio.wav'
  with wave.open(str(self.audio),'wb') as w:w.setnchannels(1);w.setsampwidth(2);w.setframerate(1000);w.writeframes(struct.pack('<1000h',*([0]*500+[32767]*500)))
  atomic_json(folder/'metadata.json',{'kind':'music','synthetic_test_artifact':False,'prompt':'<script>alert(1)</script>'})
  self.reviews=self.root/'reviews';self.client=TestClient(create_review_app(self.art,self.reviews))
 def tearDown(self):self.tmp.cleanup()
 def test_dsp_known_signal(self):
  d=measure_audio(self.audio);self.assertEqual(d['duration_seconds'],1);self.assertEqual(d['silence_seconds'],.5);self.assertEqual(d['full_scale_sample_count'],500);self.assertIsNone(d['integrated_lufs']);self.assertAlmostEqual(d['sample_peak_dbfs'],0,places=2)
 def test_read_only_media_range_and_security(self):
  self.assertEqual(self.client.get('/review').status_code,200)
  page=self.client.get('/review');self.assertIn("script-src 'self'",page.headers['content-security-policy']);self.assertNotIn('<script>alert',page.text)
  rows=self.client.get('/v1/reviews').json()['tracks'];self.assertEqual(rows[0]['status'],'unreviewed');self.assertIsNone(rows[0]['total_score'])
  part=self.client.get(f'/v1/reviews/{self.aid}/audio',headers={'Range':'bytes=0-43'});self.assertEqual(part.status_code,206);self.assertEqual(len(part.content),44)
  self.assertEqual(self.client.post('/v1/jobs',json={}).status_code,404)
  self.assertEqual(self.client.get('/v1/reviews',headers={'Host':'evil.example'}).status_code,400)
  self.assertNotEqual(self.client.get('/v1/reviews/../../etc/passwd/audio').status_code,200)
  self.assertEqual(self.client.get('/v1/reviews/'+'x'*32+'/audio').status_code,404)
 def test_legacy_excluded_from_catalog_and_audio(self):
  metadata=self.art/self.aid/'metadata.json'
  atomic_json(metadata,{'kind':'music','synthetic_test_artifact':False,'legacy':True})
  self.assertEqual(self.client.get('/v1/reviews').json()['tracks'],[])
  self.assertEqual(self.client.get(f'/v1/reviews/{self.aid}/audio').status_code,404)
 def test_custom_title_and_measured_unreviewed(self):
  atomic_json(self.art/self.aid/'metadata.json',{'kind':'music','synthetic_test_artifact':False,'title':'새 탐험곡'})
  atomic_json(self.reviews/self.aid/'result.json',{'status':'unreviewed','dsp':measure_audio(self.audio)})
  t=self.client.get('/v1/reviews').json()['tracks'][0]
  self.assertEqual(t['title'],'새 탐험곡');self.assertEqual(t['dsp']['duration_seconds'],1)
  self.assertIsNone(t['total_score']);self.assertEqual(t['status'],'unreviewed')
 def test_symlink_denied(self):
  self.audio.unlink();outside=self.root/'outside.wav';outside.write_bytes(b'secret');self.audio.symlink_to(outside)
  self.assertEqual(self.client.get(f'/v1/reviews/{self.aid}/audio').status_code,404)
 def test_bad_review_visible_not_scored(self):
  path=self.reviews/self.aid/'result.json';path.parent.mkdir(parents=True);path.write_text('{bad')
  record=self.client.get('/v1/reviews').json()['tracks'][0];self.assertEqual(record['status'],'error');self.assertIsNone(record['total_score'])
 def test_validation_rejects_fabricated_total_bad_fields_and_nulls(self):
  criteria=[{'id':'x','name':'x','max_points':100}]
  item={'criterion_id':'x','score':42.5,'evidence':[{'segment':'whole','start_seconds':None,'end_seconds':None,'observation':'a pulse','interpretation':'supports rhythm'}],'reason':'a reason','confidence':'low','uncertainty':['model hearing may be wrong']}
  obj={'scores':[item],'total_score':99};self.assertEqual(validate_score(json.dumps(obj),criteria,1)['total_score'],42.5)
  item['score']=None;self.assertIsNone(validate_score(json.dumps(obj),criteria,1)['total_score']);item['score']=42.5
  for field,value in [('score',True),('score',101),('score',42.2),('uncertainty',[7]),('uncertainty',[])]:
   bad=copy.deepcopy(obj);bad['scores'][0][field]=value
   with self.assertRaises(ValueError):validate_score(json.dumps(bad),criteria,1)
  for stamp in (-1,2,'0',True):
   bad=copy.deepcopy(obj);bad['scores'][0]['evidence'][0]['start_seconds']=stamp
   with self.assertRaises(ValueError):validate_score(json.dumps(bad),criteria,1)
  with self.assertRaises(ValueError):validate_score('',criteria,1)
if __name__=='__main__':unittest.main()
