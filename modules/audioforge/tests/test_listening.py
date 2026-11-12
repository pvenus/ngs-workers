import copy
import hashlib
import json
import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch
import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from audioforge.gateway_catalog import Catalog
from audioforge.gateway_service import create_gateway_app
from audioforge.listening_store import ListeningStore, ListeningError, GroupRequest, Mutation, LIMITS
from audioforge.listening_api import signed_headers
from audioforge.listening_bridge import create_listening_bridge
from audioforge.review_gateway import ReviewGatewayClient

class ListeningTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name)
        self.cat=Catalog(self.root/'db.sqlite3',self.root/'artifacts')
        self.candidates=[]
        for i in range(3):
            aid=uuid.uuid4().hex; directory=self.root/'artifacts'/aid; directory.mkdir(parents=True)
            (directory/'audio.wav').write_bytes(b'fixture-audio'+bytes([i]))
            song,_=self.cat.register(dict(artifact_id=aid,title='Fixture '+str(i),prompt='p',model='test',review_status='partial',review={'dsp':{'rms':0}},score={'technical':None}))
            self.candidates.append(dict(song_id=song['song_id'],artifact_id=aid,audio_sha256=song['file_sha256'],generation_task_id=str(uuid.uuid4())))
        self.store=ListeningStore(self.cat)
        self.request=dict(title='Fixture',comparison_task_id=str(uuid.uuid4()),human_listening_task_id=str(uuid.uuid4()),candidates=self.candidates,expected_revision=0,idempotency_key='fixture-group')
        self.group=self.store.save_group(GroupRequest(**self.request));self.gid=self.group['group_id'];self.sid=self.candidates[0]['song_id']
        self.original=self.cat.list(True)
    def tearDown(self): self.temp.cleanup()
    def state(self):return self.store.get(self.gid,self.sid,True)
    def payload(self,**kw):
        candidate=self.candidates[0]
        return Mutation(**(dict(expected_group_revision=self.store.group(self.gid)['group_revision'],artifact_id=candidate['artifact_id'],audio_sha256=candidate['audio_sha256'],expected_revision=self.state()['revision'],idempotency_key=uuid.uuid4().hex)|kw))
    def write(self,**kw): return self.store.mutate(self.gid,self.sid,self.payload(**kw),trusted_ui=True,source_request_id=uuid.uuid4().hex)
    def test_empty_scores_zero_partial_total_and_clear(self):
        s=self.state();self.assertFalse(s['user_evaluated']);self.assertIsNone(s['human_review']['total_score'])
        self.assertTrue(all(c['score'] is None for t in self.store.tracks(self.gid)['tracks'] for c in t['human_review']['criteria'].values()))
        s=self.write(review={'criteria':{'brief_fit':{'score':0}}});self.assertEqual(s['human_review']['criteria']['brief_fit']['score'],0);self.assertIsNone(s['human_review']['total_score']);self.assertTrue(s['user_evaluated'])
        s=self.write(review={'criteria':{k:{'score':v} for k,v in LIMITS.items()}});self.assertEqual(s['human_review']['total_score'],100)
        s=self.write(review={'criteria':{k:{'score':None} for k in LIMITS}});self.assertFalse(s['user_evaluated']);self.assertIsNone(s['human_review']['total_score']);self.assertEqual(len(self.state()['history']),3)
    def test_partial_patch_comment_and_declaration(self):
        self.write(review={'comment':'real input','criteria':{'brief_fit':{'evidence':'original words','score':0}},'listening_declaration':'partial'})
        s=self.write(review={'criteria':{'brief_fit':{'score':1.5}}})
        self.assertEqual(s['human_review']['comment'],'real input');self.assertEqual(s['human_review']['criteria']['brief_fit']['evidence'],'original words')
        self.write(review={'criteria':{'brief_fit':{'score':None,'evidence':None}},'comment':None})
        self.assertFalse(self.state()['user_evaluated'])
    def test_decision_revoke_restore_history_independent(self):
        s=self.write(action='approve');self.assertTrue(s['valid_approval']);self.assertFalse(s['user_evaluated']);self.assertEqual(s['human_review']['listening_declaration'],'unknown')
        self.assertEqual(s['human_review']['review_revision'],0)
        s=self.write(action='reject');self.assertFalse(s['valid_approval']);self.assertEqual(self.store.tracks(self.gid)['count'],2)
        self.assertEqual(self.store.tracks(self.gid,'rejected')['count'],1)
        s=self.write(action='restore');self.assertFalse(s['valid_approval']);self.assertEqual(s['review_decision'],'undecided');self.assertEqual(self.store.tracks(self.gid)['count'],3)
        self.assertEqual(len(self.state()['history']),3);self.assertEqual(self.cat.list(True),self.original)
    def test_review_preserves_prior_approval(self):
        self.write(action='approve');s=self.write(review={'comment':'later comment'})
        self.assertTrue(s['valid_approval']);self.assertTrue(s['user_evaluated']);self.assertEqual(s['decision_event']['revision_after'],1)
    def test_idempotency_replay_conflict_and_noop(self):
        p=self.payload(action='approve');a=self.store.mutate(self.gid,self.sid,p,trusted_ui=True,source_request_id='test')
        b=self.store.mutate(self.gid,self.sid,p,trusted_ui=True,source_request_id='test2');self.assertEqual(a,b)
        with self.assertRaises(ListeningError) as e:self.store.mutate(self.gid,self.sid,p.model_copy(update={'reason':'changed'}),trusted_ui=True,source_request_id='test3')
        self.assertEqual(e.exception.status,409)
        self.write(action='approve');self.assertEqual(self.state()['revision'],1);self.assertEqual(len(self.state()['history']),1)
    def test_stale_revision_and_bad_identity_noop_rejected(self):
        self.write(action='approve')
        for kw in [dict(expected_revision=0),dict(audio_sha256='0'*64),dict(expected_group_revision=2),dict(artifact_id='0'*32)]:
            with self.assertRaises(ListeningError) as e:self.write(action='approve',**kw)
            self.assertEqual(e.exception.status,409)
        self.assertEqual(len(self.state()['history']),1)
    def test_cross_group_and_changed_group_invalidate(self):
        self.write(action='approve')
        second=self.store.save_group(GroupRequest(**(self.request|dict(idempotency_key='other'))))
        self.assertFalse(self.store.get(second['group_id'],self.sid)['valid_approval'])
        self.store.save_group(GroupRequest(**(self.request|dict(expected_revision=1,idempotency_key='replace'))),self.gid)
        s=self.state();self.assertTrue(s['stale']);self.assertFalse(s['valid_approval']);self.assertEqual(len(s['history']),1)
        s=self.write(action='restore');self.assertFalse(s['valid_approval']);self.assertFalse(s['stale'])
        s=self.write(action='approve');self.assertTrue(s['valid_approval'])
    def test_changed_audio_and_missing_file_invalidate(self):
        self.write(action='approve')
        p=self.cat.artifact_dir/self.candidates[0]['artifact_id']/'audio.wav';p.write_bytes(b'changed')
        self.assertFalse(self.state()['valid_approval'])
        with self.assertRaises(ListeningError):self.write(action='approve')
        p.unlink();self.assertTrue(self.state()['stale'])
    def test_agent_test_and_self_asserted_actor_rejected(self):
        with self.assertRaises(ListeningError) as e:self.store.mutate(self.gid,self.sid,self.payload(action='approve'))
        self.assertEqual(e.exception.status,403)
        with self.assertRaises(ValidationError):self.payload(action='approve',actor_kind='user')
        self.store.environment='test';self.assertFalse(self.write(action='approve')['valid_approval'])
    def test_numeric_validation(self):
        for bad in ['1',True,float('nan'),float('inf'),1e308,10**400,-1,0.25,21]:
            with self.subTest(bad=bad),self.assertRaises(ValidationError):self.payload(review={'criteria':{'brief_fit':{'score':bad}}})
        for patch_value in [{'criteria':{'unknown':{'score':0}}},{'criteria':{'brief_fit':{'evidence':1}}},{'listening_declaration':'verified'},{'score':99}]:
            with self.assertRaises(ValidationError):self.payload(review=patch_value)
    def test_atomic_rollback(self):
        with patch.object(self.store,'_remember',side_effect=RuntimeError('simulated write failure')):
            with self.assertRaises(RuntimeError):self.write(action='reject',review={'comment':'rollback'})
        self.assertEqual(self.state()['revision'],0);self.assertEqual(self.state()['history'],[]);self.assertEqual(self.store.tracks(self.gid)['count'],3)
    def test_migration_backup_reopen_and_group_replay(self):
        backup=Path(str(self.cat.db_path)+'.before-listening-v1.sqlite3');self.assertTrue(backup.exists())
        with sqlite3.connect(backup) as db:self.assertEqual(db.execute('SELECT count(*) FROM songs').fetchone()[0],3)
        self.write(action='approve');reopen=ListeningStore(self.cat);self.assertTrue(reopen.get(self.gid,self.sid)['valid_approval'])
        self.assertEqual(reopen.save_group(GroupRequest(**self.request)),self.group)
        self.assertEqual(self.cat.list(True),self.original)
    def test_candidate_validation_and_single_candidate(self):
        for candidates in [[self.candidates[0]]*3,[self.candidates[0]|dict(audio_sha256='0'*64)]]:
            with self.assertRaises(ListeningError):self.store.save_group(GroupRequest(**(self.request|dict(candidates=candidates,idempotency_key=uuid.uuid4().hex))))
        g=self.store.save_group(GroupRequest(**(self.request|dict(candidates=[self.candidates[0]],idempotency_key='single'))));self.assertFalse(g['comparison_ready'])
    def test_archive_filters_stay_independent(self):
        self.cat.archive(self.sid);self.assertEqual(self.store.tracks(self.gid)['count'],2);self.assertEqual(self.store.tracks(self.gid,include_archived=True)['count'],3)
    def test_api_authority_and_errors(self):
        key=b'x'*32
        app=create_gateway_app(self.cat.db_path,self.cat.artifact_dir,listening_options=dict(key=key,task_validator=lambda _:None))
        client=TestClient(app);path=f'/v1/listening/groups/{self.gid}/tracks/{self.sid}'
        payload=self.payload(action='approve').model_dump_json().encode()
        self.assertEqual(client.post(path,content=payload,headers={'Content-Type':'application/json'}).status_code,403)
        headers=signed_headers(key,path,payload)|{'Content-Type':'application/json'}
        response=client.post(path,content=payload,headers=headers);self.assertEqual(response.status_code,200,response.text);self.assertTrue(response.json()['valid_approval'])
        bad=self.payload(action='restore',expected_revision=0).model_dump_json().encode()
        conflict=client.post(path,content=bad,headers=signed_headers(key,path,bad)|{'Content-Type':'application/json'})
        self.assertEqual(conflict.status_code,409);self.assertEqual(conflict.json()['current']['revision'],1)
        tampered=client.post(path,content=bad,headers=headers);self.assertEqual(tampered.status_code,403)
        self.assertEqual(client.get(path+'?history=true').json()['history'][0]['source'],'review_ui')
    def test_legacy_null_catalog_sha_uses_actual_file(self):
        with self.cat.connect() as db:
            db.execute('UPDATE songs SET file_sha256=NULL WHERE song_id=?',(self.sid,))
        s=self.write(action='approve');self.assertTrue(s['valid_approval'])
        self.assertIsNone(self.cat.get(self.sid)['file_sha256'])
        p=self.cat.artifact_dir/self.candidates[0]['artifact_id']/'audio.wav';p.write_bytes(b'changed')
        self.assertFalse(self.state()['valid_approval'])

    def test_event_snapshot_matches_current_review_after_total_and_clear(self):
        self.write(review={'criteria':{k:{'score':v} for k,v in LIMITS.items()}})
        s=self.state();self.assertEqual(s['human_review'],s['history'][-1]['human_review'])
        self.assertEqual(s['history'][-1]['human_review']['total_score'],100)
        self.write(review={'criteria':{'brief_fit':{'score':None}}})
        s=self.state();self.assertEqual(s['human_review'],s['history'][-1]['human_review'])
        self.assertIsNone(s['history'][-1]['human_review']['total_score'])
        self.assertEqual(s['history'][-1]['previous_human_review']['total_score'],100)
        self.store.save_group(GroupRequest(**(self.request|dict(expected_revision=1,idempotency_key='new-binding'))),self.gid)
        self.write(review={'comment':'new group input'})
        s=self.state();self.assertEqual(s['human_review'],s['history'][-1]['human_review'])
        self.assertEqual(s['history'][-1]['human_review']['group_revision'],2)

    def test_removed_member_history_remains_readable(self):
        self.write(action='approve')
        self.store.save_group(GroupRequest(**(self.request|dict(candidates=self.candidates[1:],expected_revision=1,idempotency_key='remove'))),self.gid)
        s=self.state();self.assertTrue(s['removed_from_group']);self.assertFalse(s['valid_approval']);self.assertEqual(len(s['history']),1)

    def test_concurrent_writers_and_duplicate_retries(self):
        from concurrent.futures import ThreadPoolExecutor
        import threading
        payloads=[self.payload(action='approve'),self.payload(action='reject')]
        barrier=threading.Barrier(2)
        def write(p):
            store=ListeningStore(self.cat)
            barrier.wait()
            try:return store.mutate(self.gid,self.sid,p,trusted_ui=True,source_request_id='fixture')['revision']
            except ListeningError as e:return e.status
        with ThreadPoolExecutor(2) as pool:results=list(pool.map(write,payloads))
        self.assertEqual(sorted(results),[1,409]);self.assertEqual(len(self.state()['history']),1)

    def test_api_nonfinite_422_and_full_browser_bridge(self):
        key=b'x'*32
        backend=TestClient(create_gateway_app(self.cat.db_path,self.cat.artifact_dir,listening_options=dict(key=key,task_validator=lambda _:None)))
        path=f'/v1/listening/groups/{self.gid}/tracks/{self.sid}'
        bad=self.payload(review={'criteria':{'brief_fit':{'score':0}}}).model_dump_json().replace('"score":0.0','"score":NaN')
        self.assertEqual(backend.post(path,content=bad,headers={'Content-Type':'application/json'}).status_code,422)
        def forward(request):
            response=backend.request(request.method,request.url.raw_path.decode(),content=request.content,headers=dict(request.headers))
            return httpx.Response(response.status_code,content=response.content)
        app=FastAPI();app.include_router(create_listening_bridge(ReviewGatewayClient('http://127.0.0.1:8767',httpx.MockTransport(forward)),key=key,origins={'http://testserver'}))
        browser=TestClient(app);headers={'Origin':'http://testserver','Sec-Fetch-Site':'same-origin','Content-Type':'application/json'}
        session=browser.post('/v1/listening/session',json={},headers=headers)
        headers['X-Review-CSRF']=session.json()['csrf_token']
        response=browser.post(path,json=self.payload(action='approve',review={'comment':'fixture listening'}).model_dump(mode='json'),headers=headers)
        self.assertEqual(response.status_code,200,response.text);self.assertTrue(response.json()['valid_approval'])
        response=browser.get(path+'?history=true');self.assertEqual(response.json()['human_review']['comment'],'fixture listening');self.assertEqual(len(response.json()['history']),1)

    def test_bridge_session_csrf_and_forwarding(self):
        calls=[];key=b'x'*32
        def handle(request):
            calls.append(request)
            return httpx.Response(409,json={'detail':'conflict','current':{'revision':2}})
        app=FastAPI();app.include_router(create_listening_bridge(ReviewGatewayClient('http://127.0.0.1:8767',httpx.MockTransport(handle)),key=key,origins={'http://testserver'}))
        client=TestClient(app);headers={'Origin':'http://testserver','Sec-Fetch-Site':'same-origin','Content-Type':'application/json'}
        self.assertEqual(client.post('/v1/listening/session',json={}).status_code,403)
        session=client.post('/v1/listening/session',json={},headers=headers);self.assertEqual(session.status_code,200)
        self.assertIn('HttpOnly',session.headers['set-cookie']);self.assertIn('SameSite=strict',session.headers['set-cookie'])
        path=f'/v1/listening/groups/{self.gid}/tracks/{self.sid}'
        self.assertEqual(client.post(path,json={},headers=headers).status_code,403)
        h=headers|{'X-Review-CSRF':session.json()['csrf_token']}
        self.assertEqual(client.post(path,json={},headers=h|{'Origin':'http://evil.test'}).status_code,403)
        response=client.post(path,json={'action':'approve'},headers=h)
        self.assertEqual(response.status_code,409);self.assertEqual(response.json()['current']['revision'],2)
        self.assertEqual(len(calls),1);self.assertIn('x-review-signature',calls[0].headers)

if __name__=='__main__':unittest.main()
