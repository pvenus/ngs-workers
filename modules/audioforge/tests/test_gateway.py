import hashlib,struct,tempfile,unittest,wave
from pathlib import Path
from fastapi.testclient import TestClient
from audioforge.gateway_service import create_gateway_app
class Fake:
 def __init__(self):self.calls=[]
 def health(self):return {'ready':True,'concurrency':1}
 def submit(self,p,k):self.calls.append((p,k));return {'id':'j1','status':'queued','artifact_id':None}
 def status(self,j):return {'id':j,'status':'running','artifact_id':None}
 def cancel(self,j):return {'id':j,'status':'cancelled','artifact_id':None}
class T(unittest.TestCase):
 def setUp(self):
  self.t=tempfile.TemporaryDirectory();r=Path(self.t.name);self.art=r/'artifacts';self.art.mkdir();self.aid='a'*32;f=self.art/self.aid;f.mkdir();self.wav=f/'audio.wav'
  with wave.open(str(self.wav),'wb') as w:w.setnchannels(2);w.setsampwidth(2);w.setframerate(48000);w.writeframes(struct.pack('<200h',*([1]*200)))
  self.fake=Fake();self.c=TestClient(create_gateway_app(r/'q.sqlite3',self.art,self.fake))
 def tearDown(self):self.t.cleanup()
 def reg(self):return {'artifact_id':self.aid,'title':'A','prompt':'p','model':'m','seed':1,'review_status':'unreviewed','score':{'bad':1},'total_score':99,'idempotency_key':'a'}
 def test_register_idempotent_null_score(self):
  a=self.c.post('/v1/gateway/tracks/register',json=self.reg()).json();b=self.c.post('/v1/gateway/tracks/register',json=self.reg()).json();self.assertTrue(a['created']);self.assertFalse(b['created']);self.assertIsNone(a['song']['score']);self.assertEqual(a['song']['availability_status'],'available');self.assertEqual(a['song']['file_sha256'],hashlib.sha256(self.wav.read_bytes()).hexdigest())
 def test_dry_run_no_job(self):
  p={'title':'x','brief':{},'prompt':'p','duration_seconds':30,'seed':1,'model':'m','idempotency_key':'dry','dry_run':True};o=self.c.post('/v1/gateway/generate',json=p).json();self.assertTrue(o['dry_run']);self.assertFalse(o['submitted']);self.assertEqual(self.fake.calls,[]);self.assertEqual(self.c.get('/v1/gateway/tracks').json()['tracks'],[])
 def test_route_cancel(self):
  p={'title':'x','brief':{},'prompt':'p','duration_seconds':30,'seed':1,'model':'m','idempotency_key':'live'};o=self.c.post('/v1/gateway/generate',json=p).json();self.assertEqual(o['job']['id'],'j1');self.assertEqual(self.c.delete('/v1/gateway/jobs/j1').json()['song']['generation_status'],'cancelled')
 def test_selection_confirmation_archive(self):
  s=self.c.post('/v1/gateway/tracks/register',json=self.reg()).json()['song']['song_id'];self.assertEqual(self.c.patch(f'/v1/gateway/tracks/{s}',json={'adoption_status':'selected'}).status_code,409);self.assertEqual(self.c.patch(f'/v1/gateway/tracks/{s}',json={'adoption_status':'selected','user_confirmed_selection':True}).json()['adoption_status'],'selected');self.c.post(f'/v1/gateway/tracks/{s}/archive');self.assertEqual(self.c.get('/v1/gateway/tracks').json()['tracks'],[]);self.assertEqual(len(self.c.get('/v1/gateway/tracks?include_archived=true').json()['tracks']),1)
 def test_symlink_denied(self):
  self.wav.unlink();x=Path(self.t.name)/'x';x.write_bytes(b'x');self.wav.symlink_to(x);self.assertEqual(self.c.post('/v1/gateway/tracks/register',json=self.reg()).status_code,422)
 def test_tags_archive_restore_library_projection(self):
  payload=self.reg();payload['title']='타이틀 음원';s=self.c.post('/v1/gateway/tracks/register',json=payload).json()['song']['song_id']
  self.assertEqual(self.c.get('/v1/reviews').json()['tracks'][0]['tags'],['타이틀'])
  self.assertEqual(self.c.put(f'/v1/gateway/tracks/{s}/tags',json={'tags':['#후보','후보',' 새벽 ']}).json()['tags'],['후보','새벽'])
  self.assertEqual(self.c.put(f'/v1/gateway/tracks/{s}/tags',json={'tags':['']}).status_code,422)
  self.c.post(f'/v1/gateway/tracks/{s}/archive')
  self.assertEqual(self.c.get('/v1/reviews').json()['tracks'],[])
  archived=self.c.get('/v1/reviews?include_archived=true').json()['tracks'][0]
  self.assertTrue(archived['archived']);self.assertEqual(archived['tags'],['후보','새벽'])
  self.c.post(f'/v1/gateway/tracks/{s}/restore')
  self.assertFalse(self.c.get('/v1/reviews').json()['tracks'][0]['archived'])
if __name__=='__main__':unittest.main()
