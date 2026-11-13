from __future__ import annotations
import hashlib,json,re,sqlite3,threading,uuid
from datetime import datetime,timezone
from pathlib import Path
from typing import Any
ID=re.compile(r'^[0-9a-f]{32}$')
GEN={'queued','running','completed','failed','cancelled'};REV={'unreviewed','pending','completed','failed','partial'};ADOPT={'undecided','selected','rejected'}
SCHEMA='''CREATE TABLE IF NOT EXISTS songs(song_id TEXT PRIMARY KEY,idempotency_key TEXT UNIQUE,generation_job_id TEXT,artifact_id TEXT UNIQUE,title TEXT NOT NULL,use_case TEXT,brief_id TEXT,brief_version TEXT,brief_json TEXT NOT NULL,prompt TEXT NOT NULL,prompt_version TEXT NOT NULL,seed INTEGER,model TEXT NOT NULL,runtime_revision TEXT,generation_settings_json TEXT NOT NULL,source_metadata_json TEXT NOT NULL,file_name TEXT NOT NULL,file_sha256 TEXT,generation_status TEXT NOT NULL,review_id TEXT,rubric_version TEXT,review_status TEXT NOT NULL,review_json TEXT,score_json TEXT,total_score REAL,adoption_status TEXT NOT NULL,archived INTEGER NOT NULL DEFAULT 0,availability_status TEXT NOT NULL,availability_error TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);CREATE INDEX IF NOT EXISTS songs_job ON songs(generation_job_id);CREATE TABLE IF NOT EXISTS song_tags(song_id TEXT PRIMARY KEY REFERENCES songs(song_id),tags_json TEXT NOT NULL);'''
def now():return datetime.now(timezone.utc).isoformat()
def jt(v):return json.dumps(v,ensure_ascii=False,separators=(',',':'),allow_nan=False)
def safe_file(root:Path,aid:str,name='audio.wav'):
 if not ID.fullmatch(aid):raise ValueError('invalid artifact id')
 root=root.resolve();parent=root/aid;path=parent/name;resolved=path.resolve()
 if not resolved.is_relative_to(root) or parent.is_symlink() or path.is_symlink():raise ValueError('unsafe artifact path')
 return resolved
def sha(path):
 h=hashlib.sha256()
 with path.open('rb') as f:
  while b:=f.read(1048576):h.update(b)
 return h.hexdigest()
class Catalog:
 def __init__(self,db_path,artifact_dir):
  self.db_path=Path(db_path);self.artifact_dir=Path(artifact_dir);self.lock=threading.RLock();self.db_path.parent.mkdir(parents=True,exist_ok=True)
  with self.connect() as db:db.executescript(SCHEMA)
 def connect(self):
  db=sqlite3.connect(self.db_path,timeout=30,check_same_thread=False);db.row_factory=sqlite3.Row;return db
 def decode(self,row):
  x=dict(row)
  for k in ('brief_json','generation_settings_json','source_metadata_json','review_json','score_json'):
   v=x.pop(k);x[k[:-5]]=json.loads(v) if v is not None else None
  x['archived']=bool(x['archived'])
  with self.connect() as db:tag_row=db.execute('SELECT tags_json FROM song_tags WHERE song_id=?',(x['song_id'],)).fetchone()
  x['tags']=json.loads(tag_row['tags_json']) if tag_row else self.infer_tags(x)
  return x
 @staticmethod
 def clean_tags(tags):
  if not isinstance(tags,list) or len(tags)>20:raise ValueError('tags must be an array of at most 20 strings')
  result=[]
  for value in tags:
   if not isinstance(value,str):raise ValueError('tag must be a string')
   tag=value.strip().lstrip('#').strip()
   if not tag or len(tag)>40 or any(c in tag for c in '\r\n\t,'):raise ValueError('invalid tag')
   if tag not in result:result.append(tag)
  return result
 @staticmethod
 def infer_tags(song):
  brief=song.get('brief') or {};raw=brief.get('hashtags',brief.get('tags',[]))
  if isinstance(raw,str):raw=raw.split()
  tags=Catalog.clean_tags(raw) if isinstance(raw,list) else []
  title=' '.join(str(song.get(k) or '') for k in ('title','use_case'))
  if ('타이틀' in title or 'title' in title.lower()) and '타이틀' not in tags:tags.insert(0,'타이틀')
  if ('스테이지' in title or 'stage' in title.lower()) and '스테이지' not in tags:tags.insert(0,'스테이지')
  return tags
 def set_tags(self,sid,tags):
  tags=self.clean_tags(tags);self.get(sid)
  with self.lock,self.connect() as db:
   db.execute('INSERT INTO song_tags(song_id,tags_json) VALUES(?,?) ON CONFLICT(song_id) DO UPDATE SET tags_json=excluded.tags_json',(sid,jt(tags)));db.commit()
  return self.get(sid)
 def refresh(self,x):
  aid=x.get('artifact_id')
  if not aid:status,error='pending',None
  else:
   try:p=safe_file(self.artifact_dir,aid,x.get('file_name') or 'audio.wav');status,error=('available',None) if p.is_file() else ('missing','audio file is missing')
   except ValueError as e:status,error='unsafe',str(e)
  if (status,error)!=(x['availability_status'],x['availability_error']):
   with self.connect() as db:db.execute('UPDATE songs SET availability_status=?,availability_error=?,updated_at=? WHERE song_id=?',(status,error,now(),x['song_id']));db.commit()
   x['availability_status'],x['availability_error']=status,error
  return x
 def get(self,sid):
  with self.connect() as db:r=db.execute('SELECT * FROM songs WHERE song_id=?',(sid,)).fetchone()
  if not r:raise KeyError(sid)
  return self.refresh(self.decode(r))
 def by_artifact(self,aid):
  with self.connect() as db:r=db.execute("SELECT * FROM songs WHERE artifact_id=?",(aid,)).fetchone()
  if not r:raise KeyError(aid)
  return self.refresh(self.decode(r))
 def by_job(self,jid):
  with self.connect() as db:r=db.execute('SELECT * FROM songs WHERE generation_job_id=?',(jid,)).fetchone()
  if not r:raise KeyError(jid)
  return self.refresh(self.decode(r))
 def list(self,archived=False):
  q='SELECT * FROM songs'+('' if archived else ' WHERE archived=0')+' ORDER BY created_at,song_id'
  with self.connect() as db:rows=db.execute(q).fetchall()
  return [self.refresh(self.decode(r)) for r in rows]
 def register(self,d):
  aid=d.get('artifact_id');gs=d.get('generation_status','completed' if aid else 'queued');rs=d.get('review_status','unreviewed');ad=d.get('adoption_status','undecided')
  if aid and not ID.fullmatch(aid):raise ValueError('invalid artifact id')
  if gs not in GEN or rs not in REV or ad not in ADOPT:raise ValueError('invalid lifecycle status')
  score,total=d.get('score'),d.get('total_score')
  if rs=='unreviewed':score=total=None
  p=safe_file(self.artifact_dir,aid) if aid else None;availability='available' if p and p.is_file() else ('pending' if not aid else 'missing');error=None if availability!='missing' else 'audio file is missing';fh=d.get('file_sha256')
  if p and p.is_file():
   actual=sha(p)
   if fh and fh!=actual:raise ValueError('audio sha256 mismatch')
   fh=actual
  key=d.get('idempotency_key')
  with self.lock,self.connect() as db:
   if key:
    r=db.execute('SELECT * FROM songs WHERE idempotency_key=?',(key,)).fetchone()
    if r:return self.refresh(self.decode(r)),False
   if aid:
    r=db.execute('SELECT * FROM songs WHERE artifact_id=?',(aid,)).fetchone()
    if r:return self.refresh(self.decode(r)),False
   stamp=now();sid=d.get('song_id') or uuid.uuid4().hex
   vals=(sid,key,d.get('generation_job_id'),aid,d.get('title') or 'Untitled',d.get('use_case'),d.get('brief_id'),str(d.get('brief_version','1')),jt(d.get('brief') or {}),d.get('prompt') or '',str(d.get('prompt_version','1')),d.get('seed'),d.get('model') or 'unknown',d.get('runtime_revision'),jt(d.get('generation_settings') or {}),jt(d.get('source_metadata') or {}),d.get('file_name','audio.wav'),fh,gs,d.get('review_id'),d.get('rubric_version'),rs,jt(d.get('review')) if d.get('review') is not None else None,jt(score) if score is not None else None,total,ad,int(bool(d.get('archived'))),availability,error,stamp,stamp)
   db.execute('INSERT INTO songs VALUES('+','.join('?'*31)+')',vals);db.commit()
  return self.get(sid),True
 def update(self,sid,d):
  allowed={'title','use_case','review_id','rubric_version','review_status','review','score','total_score','adoption_status'}
  if set(d)-allowed:raise ValueError('immutable or unknown field')
  cur=self.get(sid);rs=d.get('review_status',cur['review_status'])
  if rs not in REV or d.get('adoption_status',cur['adoption_status']) not in ADOPT:raise ValueError('invalid lifecycle status')
  if rs=='unreviewed':d.update(score=None,total_score=None)
  mp={'review':'review_json','score':'score_json'};cols=[];vals=[]
  for k,v in d.items():
   cols.append((mp.get(k,k))+'=?');vals.append(jt(v) if k in mp and v is not None else v)
  with self.connect() as db:db.execute('UPDATE songs SET '+','.join(cols)+',updated_at=? WHERE song_id=?',(*vals,now(),sid));db.commit()
  return self.get(sid)
 def archive(self,sid):
  self.get(sid)
  with self.connect() as db:db.execute('UPDATE songs SET archived=1,updated_at=? WHERE song_id=?',(now(),sid));db.commit()
  return self.get(sid)
 def restore(self,sid):
  self.get(sid)
  with self.connect() as db:db.execute('UPDATE songs SET archived=0,updated_at=? WHERE song_id=?',(now(),sid));db.commit()
  return self.get(sid)
 def sync(self,job):
  x=self.by_job(job['id']);updates={'generation_status':job.get('status',x['generation_status'])}
  if job.get('artifact_id'):safe_file(self.artifact_dir,job['artifact_id']);updates['artifact_id']=job['artifact_id']
  with self.connect() as db:db.execute('UPDATE songs SET '+','.join(k+'=?' for k in updates)+',updated_at=? WHERE song_id=?',(*updates.values(),now(),x['song_id']));db.commit()
  return self.get(x['song_id'])
