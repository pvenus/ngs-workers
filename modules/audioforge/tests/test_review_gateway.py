"""Local gateway facade tests with no database, filesystem scan or model."""
import unittest
import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from audioforge.review_gateway import ReviewGatewayClient, create_gateway_router

class ReviewGatewayTests(unittest.TestCase):
    def make_client(self, handle):
        app=FastAPI();app.include_router(create_gateway_router(ReviewGatewayClient(
            'http://127.0.0.1:8767',httpx.MockTransport(handle))))
        return TestClient(app)
    def test_list_comes_from_gateway(self):
        client=self.make_client(lambda r:httpx.Response(200,json={'tracks':[{'title':'gateway only'}]}))
        self.assertEqual(client.get('/v1/reviews').json()['tracks'][0]['title'],'gateway only')
    def test_range_and_missing_audio(self):
        def handle(r):
            self.assertEqual(r.headers['range'],'bytes=0-43')
            return httpx.Response(206,content=b'RIFF',headers={'Content-Range':'bytes 0-3/44','Content-Type':'audio/wav'})
        response=self.make_client(handle).get('/v1/reviews/'+'a'*32+'/audio',headers={'Range':'bytes=0-43'})
        self.assertEqual(response.status_code,206);self.assertEqual(response.headers['content-range'],'bytes 0-3/44')
        missing=self.make_client(lambda r:httpx.Response(404))
        self.assertEqual(missing.get('/v1/reviews/'+'a'*32+'/audio').status_code,404)
    def test_no_fallback_when_gateway_unavailable(self):
        def unavailable(r):raise httpx.ConnectError('offline',request=r)
        self.assertEqual(self.make_client(unavailable).get('/v1/reviews').status_code,503)
    def test_remote_origin_and_path_rejected(self):
        for url in ['https://example.org','http://user@localhost','http://localhost/v1']:
            with self.assertRaises(ValueError):ReviewGatewayClient(url)
        self.assertEqual(self.make_client(lambda r:self.fail('unexpected upstream call')).get('/v1/reviews/not-an-id/audio').status_code,404)
    def test_library_mutation_requires_same_origin_and_header(self):
        calls=[]
        def handle(request):
            calls.append((request.method,request.url.path))
            return httpx.Response(200,json={'archived':True})
        client=self.make_client(handle);path='/v1/library/'+'a'*32+'/archive'
        self.assertEqual(client.post(path).status_code,403)
        self.assertEqual(calls,[])
        response=client.post(path,headers={'Origin':'http://testserver','X-Library-Action':'1'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(calls,[('POST','/v1/gateway/tracks/'+'a'*32+'/archive')])

if __name__=='__main__':unittest.main()
