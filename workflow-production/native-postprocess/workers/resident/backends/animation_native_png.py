"""Pure ordered manifest for one native animation decode; no publication."""
import hashlib
import io
import json
import re

SCHEMA='ngs.animation-native-png-sequence.v1'
BINDING_KEYS={'requestId','npcId','generationId','posePreset','presetId','direction','sourceBindingHash'}
IDENTIFIER=re.compile(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,199}')
SHA256=re.compile(r'[a-f0-9]{64}')
POSES=('representative','idle','combat')
DIRECTIONS=('n','ne','e','se','s','sw','w','nw')
PNG_SIGNATURE=b'\x89PNG\r\n\x1a\n'


def _binding(binding):
 if type(binding)is not dict or set(binding)!=BINDING_KEYS:
  raise ValueError('ANIMATION_NATIVE_PNG_BINDING_INVALID')
 if any(type(binding[k])is not str or not IDENTIFIER.fullmatch(binding[k])
        for k in ('requestId','npcId','generationId','presetId')):
  raise ValueError('ANIMATION_NATIVE_PNG_BINDING_INVALID')
 if (binding['posePreset'] not in POSES or binding['direction'] not in DIRECTIONS
     or type(binding['sourceBindingHash'])is not str or not SHA256.fullmatch(binding['sourceBindingHash'])):
  raise ValueError('ANIMATION_NATIVE_PNG_BINDING_INVALID')
 return dict(binding)


def _complete_container(payload):
 # Pillow verifies image chunks but may stop at the IEND header without its
 # CRC. Require the actual terminal chunk and no trailing bytes.
 offset=len(PNG_SIGNATURE)
 while offset+12<=len(payload):
  length=int.from_bytes(payload[offset:offset+4],'big')
  kind=payload[offset+4:offset+8]
  end=offset+12+length
  if length>0x7fffffff or end>len(payload) or kind in (b'acTL',b'fcTL',b'fdAT'):
   return False
  if kind==b'IEND':
   return length==0 and end==len(payload) and payload[offset+8:end]==b'\xaeB`\x82'
  offset=end
 return False


def _size(payload):
 from PIL import Image
 if type(payload)is not bytes or not payload.startswith(PNG_SIGNATURE) or not _complete_container(payload):
  raise ValueError('ANIMATION_NATIVE_PNG_FRAME_INVALID')
 try:
  with Image.open(io.BytesIO(payload),formats=['PNG'])as png:
   if (png.format!='PNG' or getattr(png,'n_frames',1)!=1
       or getattr(png,'custom_mimetype',None)=='image/apng'):
    raise ValueError('animated PNG is not a native frame')
   png.verify()
  # verify() checks the PNG container, load() checks the actual raster. Neither
  # re-encodes or normalizes it; the published digest binds the original bytes.
  with Image.open(io.BytesIO(payload),formats=['PNG'])as png:
   png.load()
   return png.size
 except (OSError,ValueError,SyntaxError,Image.DecompressionBombError) as error:
  raise ValueError('ANIMATION_NATIVE_PNG_FRAME_INVALID')from error


def build_native_png_manifest(binding,frames,*,frame_count,fps):
 """Bind the original PNG bytes in producer order to trusted request timing."""
 binding=_binding(binding)
 if type(frame_count)is not int or not 2<=frame_count<=1000 or type(fps)not in (int,float) or not 0<fps<=120:
  raise ValueError('ANIMATION_NATIVE_PNG_TIMING_INVALID')
 entries=[];size=None
 for i,payload in enumerate(frames):
  if i>=frame_count:raise ValueError('ANIMATION_NATIVE_PNG_COUNT_INVALID')
  next_size=_size(payload)
  if size is not None and next_size!=size:raise ValueError('ANIMATION_NATIVE_PNG_CANVAS_MISMATCH')
  size=next_size
  h=hashlib.sha256(payload).hexdigest()
  entries.append({'index':i,'sha256':h,'bytes':len(payload),'mimeType':'image/png','assetId':h+'.png'})
 if len(entries)!=frame_count:raise ValueError('ANIMATION_NATIVE_PNG_COUNT_INVALID')
 return {'schemaVersion':SCHEMA,**binding,'frameCount':frame_count,'fps':fps,'width':size[0],'height':size[1],'frames':entries}


def validate_native_png_manifest(manifest,binding,frames,*,frame_count,fps):
 """Require exact schema/ordering/content against separately trusted inputs."""
 expected=build_native_png_manifest(binding,frames,frame_count=frame_count,fps=fps)
 try:
  matches=json.dumps(manifest,sort_keys=True,allow_nan=False)==json.dumps(expected,sort_keys=True,allow_nan=False)
 except (TypeError,ValueError):
  matches=False
 if not matches:raise ValueError('ANIMATION_NATIVE_PNG_MANIFEST_CHANGED')
 return expected
