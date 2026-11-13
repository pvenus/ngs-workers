"""Local review UI. Production data is proxied from the MCP Gateway."""
import os
import argparse
from pathlib import Path
from fastapi import APIRouter,FastAPI,HTTPException,Request
from fastapi.responses import FileResponse,JSONResponse,RedirectResponse
from pydantic import BaseModel,Field,ConfigDict
from starlette.middleware.trustedhost import TrustedHostMiddleware
from .config import ARTIFACT_DIR
from .review_data import catalog,safe_file
from .review_gateway import ReviewGatewayClient,create_gateway_router
STATIC=Path(__file__).with_name('review_static')
class BgmDraft(BaseModel):
 model_config=ConfigDict(extra='forbid')
 title:str=Field(min_length=1,max_length=300)
 prompt:str=Field(min_length=1,max_length=2000)
 use_case:str=Field(default='game background music',min_length=1,max_length=300)
 intent:str=Field(default='',max_length=2000)
 scene:str=Field(default='',max_length=2000)
 mood:str=Field(default='',max_length=500)
 duration:float=Field(default=30,ge=.25,le=30)
 seed:int=Field(default=42,ge=0,le=2**31-1)
 bpm:int|None=Field(default=None,ge=30,le=300)
 tags:list[str]=Field(default_factory=list,max_length=20)

def creation_allowed(request:Request):
 origin=request.headers.get('origin')
 expected=request.headers.get('x-forge-public-origin') or str(request.base_url).rstrip('/')
 if origin!=expected or request.headers.get('x-library-action')!='1':
  raise HTTPException(403,'음원 페이지에서만 요청할 수 있습니다.')

def create_router(artifact_dir=ARTIFACT_DIR,review_dir=None):
 artifact_dir=Path(artifact_dir);review_dir=Path(review_dir) if review_dir else artifact_dir.parent/'reviews/bgm-local-v1';router=APIRouter()
 def data():
  try:return catalog(artifact_dir,review_dir)
  except (OSError,ValueError):raise HTTPException(503,'검수 자료를 읽을 수 없습니다.')
 def track(aid):
  found=next((t for t in data()['tracks'] if t['artifact_id']==aid),None)
  if found is None:raise HTTPException(404,'BGM을 찾을 수 없습니다.')
  return found
 @router.get('/v1/reviews')
 def reviews():return data()
 @router.get('/v1/reviews/{artifact_id}')
 def review(artifact_id:str):return track(artifact_id)
 @router.get('/v1/reviews/{artifact_id}/audio')
 def audio(artifact_id:str):
  track(artifact_id)
  try:path=safe_file(artifact_dir,artifact_id,'audio.wav')
  except ValueError:raise HTTPException(404,'잘못된 오디오 경로입니다.')
  if not path.is_file():raise HTTPException(404,'오디오 파일이 없습니다.')
  return FileResponse(path,media_type='audio/wav',filename=artifact_id+'.wav')
 @router.get('/v1/reviews/{artifact_id}/download')
 def download(artifact_id:str):return JSONResponse(track(artifact_id),headers={'Content-Disposition':f'attachment; filename="{artifact_id}-review.json"'})
 return router
def create_review_app(artifact_dir=ARTIFACT_DIR,review_dir=None,gateway_url=None):
 app=FastAPI(title='AudioForge Local BGM Review',docs_url=None,redoc_url=None)
 public_host=os.getenv('AUDIOFORGE_PUBLIC_HOST','').strip()
 allowed_hosts=['127.0.0.1','localhost','testserver']
 if public_host:
  import ipaddress
  try:ipaddress.ip_address(public_host)
  except ValueError:raise ValueError('AUDIOFORGE_PUBLIC_HOST must be an IP address')
  allowed_hosts.append(public_host)
 app.add_middleware(TrustedHostMiddleware,allowed_hosts=allowed_hosts)
 @app.middleware('http')
 async def headers(request,call_next):
  response=await call_next(request);response.headers['Content-Security-Policy']="default-src 'none'; script-src 'self'; style-src 'self'; media-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'; form-action 'none'";response.headers['X-Content-Type-Options']='nosniff';response.headers['Referrer-Policy']='no-referrer';response.headers['Cache-Control']='no-store';return response
 @app.get('/',include_in_schema=False)
 def root():return RedirectResponse('/review')
 @app.get('/review',include_in_schema=False)
 def page():return FileResponse(STATIC/'library.html',media_type='text/html')
 @app.get('/review/library.js',include_in_schema=False)
 def library_js():return FileResponse(STATIC/'library.js',media_type='text/javascript')
 @app.get('/review/library.css',include_in_schema=False)
 def library_css():return FileResponse(STATIC/'library.css',media_type='text/css')
 @app.get('/review/app.js',include_in_schema=False)
 def js():return FileResponse(STATIC/'app.js',media_type='text/javascript')
 @app.get('/review/style.css',include_in_schema=False)
 def css():return FileResponse(STATIC/'style.css',media_type='text/css')
 @app.get('/review/listening.css',include_in_schema=False)
 def listening_css():return FileResponse(STATIC/'listening.css',media_type='text/css')
 @app.get('/health')
 def health():return {'ok':True,'service':'audioforge-review','read_only':not bool(gateway_url),'source':'gateway' if gateway_url else 'local-test','model_loaded':False}
 @app.get('/v1/bgm-requests/schema')
 def bgm_schema():return BgmDraft.model_json_schema()
 @app.post('/v1/bgm-requests',status_code=202)
 def submit_bgm(draft:BgmDraft,request:Request):
  creation_allowed(request)
  from .file_workflow import create_request,REQUEST_DIR
  from .gateway_catalog import Catalog
  try:
   tags=Catalog.clean_tags(draft.tags)
   path=create_request(argparse.Namespace(title=draft.title,use_case=draft.use_case,intent=draft.intent,
    scene=draft.scene,mood=draft.mood,prompt=draft.prompt,prompt_version='1',model='acestep-v15-turbo',
    duration=draft.duration,seed=draft.seed,bpm=draft.bpm,tag=tags,request_dir=REQUEST_DIR))
  except (ValueError,OSError) as exc:raise HTTPException(422,str(exc)) from exc
  return {'request_id':path.stem,'phase':'queued','status_url':f'/v1/bgm-requests/{path.stem}'}
 @app.get('/v1/bgm-requests/{request_id}')
 def bgm_status(request_id:str):
  from .file_workflow import REQUEST_DIR,STATE_DIR
  from .review_data import load_json
  if len(request_id)!=32 or any(ch not in '0123456789abcdef' for ch in request_id):raise HTTPException(400,'잘못된 요청 ID입니다.')
  if not (REQUEST_DIR/f'{request_id}.json').is_file():raise HTTPException(404,'요청을 찾을 수 없습니다.')
  state=STATE_DIR/f'{request_id}.json'
  return load_json(state) if state.is_file() else {'request_id':request_id,'phase':'queued'}
 production=Path(artifact_dir).resolve()==Path(ARTIFACT_DIR).resolve() and review_dir is None
 if gateway_url is None and production:gateway_url=os.getenv('AUDIOFORGE_GATEWAY_URL','http://127.0.0.1:8767')
 app.include_router(create_gateway_router(ReviewGatewayClient(gateway_url)) if gateway_url else create_router(artifact_dir,review_dir))
 if gateway_url:
  from .listening_bridge import create_listening_bridge
  app.include_router(create_listening_bridge(ReviewGatewayClient(gateway_url)))
 return app
app=create_review_app()
def main():
 import uvicorn;uvicorn.run('audioforge.review_api:app',host='127.0.0.1',port=8766,workers=1)
if __name__=='__main__':main()
