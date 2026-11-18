"""A single transform/palette across every frame and direction; no per-frame recentering."""
import hashlib,io,math
from pathlib import Path
from PIL import Image

def png(image):
 out=io.BytesIO();image.save(out,format='PNG');return out.getvalue()

def process_set(directions,folder,margin_ratio=.1,pixel_scale=1):
 if pixel_scale not in (1,.5):raise ValueError('FRAMES_PIXEL_SCALE_INVALID')
 folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
 bounds=[];images=[];size=None
 for direction,item in directions.items():
  if len(item['rawFrames'])!=len(item['alphaFrames'])or not item['rawFrames']:raise ValueError('FRAMES_ALPHA_COUNT_MISMATCH')
  for index,path in enumerate(item['alphaFrames']):
   image=Image.open(path).convert('RGBA')
   if size and image.size!=size:raise ValueError('FRAMES_SOURCE_DIMENSIONS_MISMATCH')
   size=image.size
   # Explicit binary alpha policy. Never infer alpha from RGB/black clothing.
   alpha=image.getchannel('A').point(lambda x:255 if x>=128 else 0)
   bbox=alpha.getbbox()
   if bbox is None:raise ValueError('FRAMES_EMPTY_FOREGROUND')
   if bbox[0]<=0 or bbox[1]<=0 or bbox[2]>=size[0]or bbox[3]>=size[1]:raise ValueError('FRAMES_FOREGROUND_TOUCHES_SOURCE_EDGE')
   image.putalpha(alpha);bounds.append(bbox);images.append((direction,index,image,bbox))
 union=(min(b[0]for b in bounds),min(b[1]for b in bounds),max(b[2]for b in bounds),max(b[3]for b in bounds))
 w,h=union[2]-union[0],union[3]-union[1];pad=max(4,math.ceil(max(w,h)*margin_ratio))
 # Preserve source pixels; only crop common unused background and pad. No arbitrary upscale.
 source_canvas=(w+2*pad,h+2*pad);canvas=tuple(math.ceil(v*pixel_scale)for v in source_canvas);samples=[]
 scale_x,scale_y=canvas[0]/source_canvas[0],canvas[1]/source_canvas[1]
 def prepared(im):
  crop=im.crop(union)
  if pixel_scale==1:return crop
  # Resize original RGBA before quantization, using premultiplied-alpha BOX filtering.
  placed=Image.new('RGBA',source_canvas,(0,0,0,0));placed.paste(crop,(pad,pad))
  reduced=placed.resize(canvas,Image.Resampling.BOX)
  reduced.putalpha(reduced.getchannel('A').point(lambda a:255 if a>=128 else 0))
  if reduced.getchannel('A').getbbox() is None:raise ValueError('FRAMES_EMPTY_SCALED_FOREGROUND')
  return reduced
 for _,_,im,_ in images:
  crop=prepared(im);rgb=crop.convert('RGB');alpha=crop.getchannel('A')
  pixels=[p for p,a in zip(rgb.getdata(),alpha.getdata())if a]
  budget=max(32,262144//len(images));samples.extend(pixels[::max(1,math.ceil(len(pixels)/budget))])
 # Stable shared palette removes per-frame quantization palette churn.
 stride=max(1,len(samples)//262144);samples=samples[::stride]
 sample=Image.new('RGB',(len(samples),1));sample.putdata(samples)
 pal=sample.quantize(colors=32,method=Image.Quantize.MEDIANCUT,dither=Image.Dither.NONE)
 palette=pal.getpalette()[:96];palette+=palette[:3]*((768-len(palette))//3)
 palette_image=Image.new('P',(1,1));palette_image.putpalette(palette)
 out={d:{'frameCount':len(v['rawFrames']),'fps':v['fps'],'sourceAssetId':v['sourceAssetId'],'frames':[],'alphaTrace':v.get('trace',{})}for d,v in directions.items()};artifacts={}
 for direction,index,im,bbox in images:
  crop=prepared(im);quant=crop.convert('RGB').quantize(palette=palette_image,dither=Image.Dither.NONE).convert('RGBA');quant.putalpha(crop.getchannel('A'))
  result=Image.new('RGBA',canvas,(0,0,0,0));result.paste(quant,(pad,pad)if pixel_scale==1 else(0,0))
  # Canonical transparent RGB prevents matte fringes in consumers mishandling alpha.
  result.paste((0,0,0,0),(0,0,*canvas),result.getchannel('A').point(lambda a:255-a))
  raw=Path(directions[direction]['rawFrames'][index]).read_bytes();alpha_raw=Path(directions[direction]['alphaFrames'][index]).read_bytes();processed=png(result)
  alpha_hash=hashlib.sha256(alpha_raw).hexdigest();raw_hash=hashlib.sha256(raw).hexdigest();out_hash=hashlib.sha256(processed).hexdigest()
  for sha,data in ((raw_hash,raw),(alpha_hash,alpha_raw),(out_hash,processed)):
   target=folder/(sha+'.png');target.write_bytes(data);artifacts[sha]=str(target)
  out[direction]['frames'].append({'index':index,'sourceTimestamp':directions[direction].get('frameTimes',[i/directions[direction]['fps']for i in range(len(directions[direction]['rawFrames']))])[index],'assetId':out_hash+'.png','rawAssetId':raw_hash+'.png','alphaAssetId':alpha_hash+'.png','sourceBounds':list(bbox)})
 return {'schemaVersion':'ngs.npc-animation-frame-set.v1','directions':out,'canvas':{'width':canvas[0],'height':canvas[1],'unionBounds':list(union),'sourceWidth':size[0],'sourceHeight':size[1],'paddingPixels':math.ceil(pad*pixel_scale),'marginRatio':margin_ratio,'scale':pixel_scale,'scaleX':scale_x,'scaleY':scale_y,'sourceCanvasWidth':source_canvas[0],'sourceCanvasHeight':source_canvas[1],'offsetX':(pad-union[0])*scale_x,'offsetY':(pad-union[1])*scale_y,'pivot':{'x':(size[0]/2-union[0]+pad)*scale_x,'y':(union[3]-union[1]+pad)*scale_y}},'pixelPolicy':{'pixelScale':pixel_scale,'resizeBeforeQuantization':True,'resizeFilter':'BOX'if pixel_scale!=1 else'none','colors':32,'alphaThreshold':128,'dither':False,'paletteScope':'all-directions-all-frames','palette':palette[:96]},'artifacts':artifacts}
