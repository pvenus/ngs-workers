from __future__ import annotations
import os
from pathlib import Path
from typing import Any
import httpx
from fastapi import FastAPI,HTTPException,Query
from fastapi.responses import FileResponse,JSONResponse
from pydantic import BaseModel,Field
from starlette.middleware.trustedhost import TrustedHostMiddleware
from .config import ARTIFACT_DIR,DB_PATH
from .gateway_catalog import Catalog,safe_file
class Generate(BaseModel):
 title:str=Field(min_length=1,max_length=300);brief:dict[str,Any]=Field(default_factory=dict);brief_id:str|None=None;brief_version:str='1';prompt:str=Field(min_length=1,max_length=4000);prompt_version:str='1';duration_seconds:float=Field(ge=.25,le=600);seed:int=Field(ge=0);model:str;revision:str|None=None;generation_settings:dict[str,Any]=Field(default_factory=dict);idempotency_key:str=Field(min_length=1,max_length=200);dry_run:bool=False
class Register(BaseModel):
 song_id:str|None=None;idempotency_key:str|None=None;generation_job_id:str|None=None;artifact_id:str;title:str;use_case:str|None=None;brief_id:str|None=None;brief_version:str='1';brief:dict[str,Any]=Field(default_factory=dict);prompt:str;prompt_version:str='1';seed:int|None=None;model:str;runtime_revision:str|None=None;generation_settings:dict[str,Any]=Field(default_factory=dict);source_metadata:dict[str,Any]=Field(default_factory=dict);file_sha256:str|None=None;review_id:str|None=None;rubric_version:str|None=None;review_status:str='unreviewed';review:dict[str,Any]|None=None;score:dict[str,Any]|None=None;total_score:float|None=None;adoption_status:str='undecided'
class Update(BaseModel):
 title:str|None=None;use_case:str|None=None;review_id:str|None=None;rubric_version:str|None=None;review_status:str|None=None;review:dict[str,Any]|None=None;score:dict[str,Any]|None=None;total_score:float|None=None;adoption_status:str|None=None;user_confirmed_selection:bool=False
class TagsUpdate(BaseModel):
 tags:list[str]
class Generation:
 def __init__(self,url=None):self.url=(url or os.getenv('AUDIOFORGE_API_URL','http://127.0.0.1:8765')).rstrip('/')
 def req(self,m,p,**kw):
  with httpx.Client(base_url=self.url,timeout=15) as c:r=c.request(m,p,**kw)
  if r.is_error:raise RuntimeError(f'generation service {r.status_code}: {r.text}')
  return r.json()
 def health(self):return self.req('GET','/ready')
 def submit(self,x,k):return self.req('POST','/v1/jobs',json=x,headers={'Idempotency-Key':k})
 def status(self,x):return self.req('GET',f'/v1/jobs/{x}')
 def cancel(self,x):return self.req('DELETE',f'/v1/jobs/{x}')
def projection(x):
 return {'song_id':x['song_id'],'artifact_id':x.get('artifact_id'),'title':x['title'],'audio_url':f"/v1/reviews/{x['song_id']}/audio",'audio_available':x['availability_status']=='available','generation':x['source_metadata'],'generation_status':x['generation_status'],'status':x['review_status'],'review_status':x['review_status'],'review':x['review'],'score':x['score'],'total_score':x['total_score'],'adoption_status':x['adoption_status'],'archived':x['archived'],'tags':x['tags'],'availability_status':x['availability_status'],'availability_error':x['availability_error']}
def create_gateway_app(db_path=DB_PATH,artifact_dir=ARTIFACT_DIR,generation=None,*,listening_options=None):
 cat=Catalog(db_path,artifact_dir);gen=generation or Generation();app=FastAPI(title='AudioForge MCP Gateway',version='1.0.0');app.add_middleware(TrustedHostMiddleware,allowed_hosts=['127.0.0.1','localhost','testserver']);app.state.catalog=cat
 @app.middleware('http')
 async def headers(req,call):
  r=await call(req);r.headers['Cache-Control']='no-store';r.headers['X-Content-Type-Options']='nosniff';return r
 @app.get('/health')
 def health():
  try:up=gen.health()
  except Exception as e:up={'ready':False,'error':type(e).__name__}
  return {'ok':True,'service':'audioforge-gateway','local_only':True,'catalog':'sqlite','generation':up}
 @app.post('/v1/gateway/generate',status_code=202)
 def generate(r:Generate):
  d=r.model_dump()
  if r.dry_run:return JSONResponse({'dry_run':True,'valid':True,'submitted':False,'job_id':None,'artifact_id':None,'idempotency_key':r.idempotency_key,'route':'gateway -> generation-service','normalized_request':d},status_code=200)
  try:j=gen.submit({'prompt':r.prompt,'duration_seconds':r.duration_seconds,'seed':r.seed,'model':r.model,'kind':'music','generation_settings':r.generation_settings},r.idempotency_key)
  except RuntimeError as e:raise HTTPException(503,str(e))
  song,created=cat.register({'idempotency_key':r.idempotency_key,'generation_job_id':j['id'],'title':r.title,'brief':r.brief,'brief_id':r.brief_id,'brief_version':r.brief_version,'prompt':r.prompt,'prompt_version':r.prompt_version,'seed':r.seed,'model':r.model,'runtime_revision':r.revision,'generation_settings':r.generation_settings,'generation_status':j['status'],'source_metadata':{'gateway_request':d}})
  return {'created':created,'dry_run':False,'job':j,'song':song}
 @app.get('/v1/gateway/jobs/{jid}')
 def job(jid):
  try:j=gen.status(jid)
  except RuntimeError as e:raise HTTPException(503,str(e))
  try:s=cat.sync(j)
  except KeyError:s=None
  return {'job':j,'song':s}
 @app.delete('/v1/gateway/jobs/{jid}')
 def cancel(jid):
  try:j=gen.cancel(jid)
  except RuntimeError as e:raise HTTPException(503,str(e))
  try:s=cat.sync(j)
  except KeyError:s=None
  return {'job':j,'song':s}
 @app.post('/v1/gateway/tracks/register')
 def register(r:Register):
  try:s,c=cat.register({**r.model_dump(),'generation_status':'completed'})
  except ValueError as e:raise HTTPException(422,str(e))
  return {'created':c,'song':s}
 @app.get('/v1/gateway/tracks')
 def tracks(include_archived:bool=Query(False)):return {'schema_version':1,'source_of_truth':'gateway-sqlite','tracks':cat.list(include_archived)}
 @app.get('/v1/gateway/tracks/{sid}')
 def track(sid):
  try:return cat.get(sid)
  except KeyError:raise HTTPException(404,'song not found')
 @app.patch('/v1/gateway/tracks/{sid}')
 def update(sid,r:Update):
  d=r.model_dump(exclude_unset=True);confirmed=d.pop('user_confirmed_selection',False)
  if d.get('adoption_status')=='selected' and not confirmed:raise HTTPException(409,'selection requires explicit user confirmation')
  try:return cat.update(sid,d)
  except KeyError:raise HTTPException(404,'song not found')
  except ValueError as e:raise HTTPException(422,str(e))
 @app.post('/v1/gateway/tracks/{sid}/archive')
 def archive(sid):
  try:return cat.archive(sid)
  except KeyError:raise HTTPException(404,'song not found')
 @app.post('/v1/gateway/tracks/{sid}/restore')
 def restore(sid):
  try:return cat.restore(sid)
  except KeyError:raise HTTPException(404,'song not found')
 @app.put('/v1/gateway/tracks/{sid}/tags')
 def set_tags(sid,r:TagsUpdate):
  try:return cat.set_tags(sid,r.tags)
  except KeyError:raise HTTPException(404,'song not found')
  except ValueError as e:raise HTTPException(422,str(e))
 @app.get('/v1/gateway/tracks/{sid}/audio')
 def audio(sid):
  try:s=cat.get(sid)
  except KeyError:raise HTTPException(404,'song not found')
  if s['availability_status']!='available' or not s.get('artifact_id'):raise HTTPException(404,s.get('availability_error') or 'audio unavailable')
  try:p=safe_file(Path(artifact_dir),s['artifact_id'],s['file_name'])
  except ValueError:raise HTTPException(404,'unsafe audio path')
  return FileResponse(p,media_type='audio/wav',filename=sid+'.wav')
 @app.get('/v1/reviews')
 def reviews(include_archived:bool=Query(False)):return {'schema_version':1,'notice':'Gateway catalog projection; unreviewed scores are null.','tracks':[projection(x) for x in cat.list(include_archived)]}
 def review_lookup(key):
  try:return cat.get(key)
  except KeyError:
   try:return cat.by_artifact(key)
   except KeyError:raise HTTPException(404,'song not found')
 @app.get('/v1/reviews/{sid}')
 def review(sid):return projection(review_lookup(sid))
 @app.get('/v1/reviews/{sid}/audio')
 def review_audio(sid):
  s=review_lookup(sid);return audio(s['song_id'])
 @app.get('/v1/reviews/{sid}/download')
 def download(sid):return JSONResponse(review(sid),headers={'Content-Disposition':f'attachment; filename="{sid}-review.json"'})
 from .listening_api import install_listening
 install_listening(app,cat,**(listening_options or {}))
 return app
app=create_gateway_app()
def main():
 import uvicorn;uvicorn.run('audioforge.gateway_service:app',host='127.0.0.1',port=8767,workers=1)
