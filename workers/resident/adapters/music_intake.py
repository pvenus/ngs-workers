"""Request-service-only new HQ music intake; never imported by worker runtime.

Explicit schema installation. No historical AudioForge records are migrated.
Target/revision binding is separate from the original HQ DTO and must be read
from this authority by PDS, not inferred from client-supplied provenance.
"""
import json,re
from ..backends.image_music_workspace import digest
TYPE='audio.music.generate'
SCHEMA='audio-music-hq.v1'
DOMAIN='audio.music'
class MusicIntake:
 def __init__(self,common):self.common=common
 def initialize_schema(self):
  with self.common.tx():self.common.db.execute('CREATE TABLE IF NOT EXISTS resident_music_payloads(request_id TEXT PRIMARY KEY,actor TEXT NOT NULL,request_json TEXT NOT NULL,input_hash TEXT NOT NULL,binding_json TEXT NOT NULL)')
 def accept(self,actor,key,project_id,target_id,expected_revision,request):
  from modules.audioforge.audioforge.hq_contract import validate_payload
  validate_payload(request)
  if any(not isinstance(v,str)or not re.fullmatch(r'[A-Za-z0-9._:-]{1,180}',v)for v in (actor,key,project_id,target_id))or not isinstance(expected_revision,str)or not expected_revision:raise ValueError('MUSIC_INTAKE_BINDING_INVALID')
  raw=json.dumps(request,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
  binding=json.dumps({'projectId':project_id,'targetId':target_id,'expectedRevision':expected_revision},sort_keys=True,separators=(',',':'))
  rid='audio.music.'+digest([actor,key,project_id])[:48];h=digest(request)
  with self.common.tx():
   old=self.common.db.execute('SELECT actor,request_json,input_hash,binding_json FROM resident_music_payloads WHERE request_id=?',(rid,)).fetchone()
   if old and tuple(old)!=(actor,raw,h,binding):raise ValueError('MUSIC_INTAKE_IDEMPOTENCY_CONFLICT')
   self.common.db.execute('INSERT OR IGNORE INTO resident_music_payloads VALUES(?,?,?,?,?)',(rid,actor,raw,h,binding))
   return self.common.accept({'requestId':rid,'projectId':project_id,'requestType':TYPE,'schemaVersion':SCHEMA,'source':'audio.music','requester':actor,'idempotencyKey':key,'payloadRef':{'domain':DOMAIN,'schemaVersion':SCHEMA,'referenceId':rid,'inputHash':h},'createdAt':self.common.clock()})
 def payload(self,rid):
  row=self.common.row(rid)
  if row['request_type']!=TYPE or row['schema_version']!=SCHEMA:raise ValueError('MUSIC_REQUEST_TYPE_INVALID')
  p=self.common.db.execute('SELECT * FROM resident_music_payloads WHERE request_id=?',(rid,)).fetchone()
  if p is None:raise ValueError('MUSIC_PAYLOAD_MISSING')
  value=dict(p);request=json.loads(value['request_json']);binding=json.loads(value['binding_json'])
  if digest(request)!=value['input_hash']or value['actor']!=row['requester']or binding['projectId']!=row['project_id']:raise ValueError('MUSIC_PAYLOAD_CORRUPT')
  return {'requestId':rid,'requestType':TYPE,'schemaVersion':SCHEMA,'domain':DOMAIN,'capability':'music.generate','parameters':request,'inputHash':value['input_hash'],'inputRefs':[],'expectedRevision':binding['expectedRevision'],'binding':binding}
