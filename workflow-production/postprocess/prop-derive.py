"""Deterministic checked prop RGBA and pixel output. No NPC processing changes."""
from collections import Counter
from functools import lru_cache
import hashlib
import io
import json
from pathlib import Path
import runpy
import struct
import sys
import zlib

from PIL import Image

MAX_PIXELS = 30_000_000
MAX_BYTES = 16 * 1024 * 1024
SIGNATURE = b'\x89PNG\r\n\x1a\n'
palette_for = runpy.run_path(str(Path(__file__).with_name('postprocess-pixel.py')))['palette_for']


def check(value, message='PROP_DERIVATION_MEDIA'):
    if not value:
        raise ValueError(message)



def filtered_idat(image):
    # Pillow chooses reversible PNG scanline filters. Keep only our RGBA
    # frame's IDAT stream, not its headers, alpha metadata or ancillary chunks.
    # The APNG framing below still uses full independent frames.
    png=io.BytesIO()
    image.save(png, format='PNG', compress_level=6)
    data=png.getbuffer()
    offset=8;parts=[]
    while offset+12 <= len(data):
        count,kind=struct.unpack_from('>I4s',data,offset)
        end=offset+count+12
        check(end<=len(data),'PROP_DERIVATION_APNG_ENCODE')
        if kind==b'IDAT':
            parts.append(data[offset+8:end-4])
        if kind==b'IEND':
            check(end==len(data) and parts,'PROP_DERIVATION_APNG_ENCODE')
            return b''.join(parts)
        offset=end
    raise ValueError('PROP_DERIVATION_APNG_ENCODE')

def encode(images, durations):
    """Retain full independent canvases, exact integer delays, and repeated frames."""
    def chunk(kind, body):
        return struct.pack('>I',len(body))+kind+body+struct.pack('>I', zlib.crc32(kind+body))
    w,h=images[0].size
    output=bytearray(SIGNATURE + chunk(b'IHDR',struct.pack('>IIBBBBB',w,h,8,6,0,0,0)))
    sequence=0
    if durations:
        output.extend(chunk(b'acTL',struct.pack('>II',len(images),1)))
    for i,image in enumerate(images):
        check(image.mode=='RGBA' and image.size==(w,h))
        if durations:
            output.extend(chunk(b'fcTL',struct.pack('>IIIIIHHBB',sequence,w,h,0,0,durations[i],1000,0,0)))
            sequence+=1
        if durations:
            compressed=filtered_idat(image)
        else:
            # Preserve the established still-byte encoding and receipt hashes.
            data=image.tobytes();stride=w*4
            lines=b''.join(b'\0'+data[j:j+stride] for j in range(0,len(data),stride))
            compressed=zlib.compress(lines,level=6)
        if i:
            output.extend(chunk(b'fdAT',struct.pack('>I',sequence)+compressed))
            sequence+=1
        else:
            output.extend(chunk(b'IDAT',compressed))
        check(len(output)+12<=MAX_BYTES,'PROP_DERIVATION_OUTPUT_BUDGET')
    output.extend(chunk(b'IEND',b''))
    return bytes(output)


def alpha_proof(image):
    alpha=image.getchannel('A')
    lo,hi=alpha.getextrema()
    check(lo==0 and hi>0,'PROP_DERIVATION_ALPHA_REQUIRED')
    return alpha


def cleared(image):
    a=alpha_proof(image)
    # Preserve partially covered edge pixels; only canonicalize fully transparent RGB.
    image.paste((0,0,0,0), (0,0,*image.size), a.point(lambda n:255 if n==0 else 0))
    return image


def fit(image,size):
    if image.size==size:return image.copy()
    # Same transform for every member; no frame-dependent trimming or recentering.
    if image.width*size[1]>=image.height*size[0]:
        target=(size[0],max(1,(image.height*size[0]+image.width//2)//image.width))
    else:
        target=(max(1,(image.width*size[1]+image.height//2)//image.height),size[1])
    canvas=Image.new('RGBA',size)
    canvas.paste(image.resize(target,Image.Resampling.LANCZOS),((size[0]-target[0])//2,(size[1]-target[1])//2))
    return canvas


def pixelize(images,size):
    scaled=[im.resize(size,Image.Resampling.BOX) for im in images]
    hist=Counter()
    stride=max(1,(size[0]*size[1]*len(scaled)+262143)//262144)
    for im in scaled:
        opacity=0
        for i,(r,g,b,a) in enumerate(im.getdata()):
            if a>=128:
                opacity+=1
                if i % stride == 0:hist[(r,g,b)]+=1
        check(0<opacity<size[0]*size[1],'PROP_DERIVATION_PIXEL_FOREGROUND')
        if not hist:
            hist[next((r,g,b) for r,g,b,a in im.getdata() if a>=128)]+=1
    palette=palette_for(hist)
    # Reuse established deterministic median cut; bounded nearest-colour cache.
    @lru_cache(maxsize=8192)
    def nearest(r,g,b):
        c=(r,g,b)
        return min(palette,key=lambda q:(sum((c[k]-q[k])**2 for k in range(3)),q))
    out=[]
    for im in scaled:
        image=Image.new('RGBA',size)
        image.putdata([(*nearest(r,g,b),255) if a>=128 else (0,0,0,0) for r,g,b,a in im.getdata()])
        out.append(image)
    return out


def trustworthy_static_rgba(raw,size):
    """Apply the native cutout policy to the exact decoded provider image.

    An alpha channel or painted checkerboard alone is not transparency. Match
    the native image verifier's conservative foreground/background thresholds;
    authentic edges are not thresholded or rematted on this path.
    """
    with Image.open(io.BytesIO(raw)) as im:
        if im.mode!='RGBA' or im.size!=size:
            return None
        im.load()  # A header/partial decode cannot prove a native cutout.
        alpha=im.getchannel('A')
        w,h=size;pixels=w*h
        hist=alpha.histogram()
        if 100*hist[0]<pixels or 100*sum(hist[250:])<pixels:
            return None
        if any(alpha.getpixel(p)!=0 for p in ((0,0),(0,h-1),(w-1,0),(w-1,h-1))):
            return None
        border=sum(alpha.crop(box).histogram()[0] for box in
                   ((0,0,w,1),(0,h-1,w,h),(0,1,1,h-1),(w-1,1,w,h-1)))
        if 10*border<9*(2*w+2*h-4):
            return None
        return cleared(im.copy())



CANDLE_CHROMA_PROFILE = 'candle-chroma-headroom-v1'


def connected_candle_chroma(image):
    # Runtime uses its pinned Pillow/NumPy/OpenCV stack. No learned model.
    import cv2
    import numpy as np
    check(image.mode=='RGB' and image.size==(512,352),'PROP_DERIVATION_CHROMA_FRAME')
    rgb=np.array(image,dtype=np.uint8)
    r,g,b=rgb.astype(np.int32).transpose(2,0,1)
    excess=g-np.maximum(r,b)
    candidate=(g>=64)&(excess>=14)&(100*excess>=20*g)
    count,labels=cv2.connectedComponents(candidate.astype(np.uint8),connectivity=4)
    border=np.concatenate((labels[0],labels[-1],labels[1:-1,0],labels[1:-1,-1]))
    exterior=np.zeros(count,dtype=bool)
    exterior[border]=True;exterior[0]=False
    keyed=exterior[labels]
    alpha=np.full(rgb.shape[:2],255,dtype=np.uint8)
    alpha[keyed]=np.clip(((105-excess)*255)//91,0,255)[keyed].astype(np.uint8)
    # Refuse inconsistent/missing green backing rather than use a learned matte.
    check(np.count_nonzero(alpha==0)>=alpha.size//100 and np.count_nonzero(alpha==255)>=alpha.size//100,
          'PROP_DERIVATION_CHROMA_FRAME')
    rgba=np.dstack((rgb,alpha))
    edge=keyed&(alpha>0)
    rgba[:,:,1][edge]=np.minimum(g,np.maximum(r,b)+12)[edge].astype(np.uint8)
    rgba[alpha==0,:3]=0
    return Image.fromarray(rgba)


def process(root,spec,matte,corridor=False):
    media=spec.get('media')
    check(not corridor or spec.get('kind')=='detail','PROP_DERIVATION_RENDER_PROFILE')
    use_chroma='renderProfile' in spec
    if use_chroma:
        check(spec['renderProfile']==CANDLE_CHROMA_PROFILE and spec.get('kind')=='detail' and
              media is None and len(spec.get('frameDurationsMs',()))>=2,'PROP_DERIVATION_RENDER_PROFILE')
    if media is not None:
        check(spec['kind'] in ('pixel','spirit'))
        payload=(root/'source.png').read_bytes()
        check(len(payload)==media['bytes'] and hashlib.sha256(payload).hexdigest()==media['sha256'],
              'PROP_DERIVATION_INPUT_CHANGED')
        with Image.open(io.BytesIO(payload)) as image:
            durations=spec['frameDurationsMs']
            check(image.mode=='RGBA' and image.n_frames==(len(durations) or 1))
            images=[]
            for i in range(image.n_frames):
                image.seek(i)
                check(image.size==(media['width'],media['height'])
                      and (not durations or image.info['duration']==durations[i]))
                images.append(cleared(image.copy()))
        canvas=spec['canvas']
        size=(canvas['width'],canvas['height'])
        if spec['kind']=='spirit':
            check(not durations and len(images)==1,'PROP_DERIVATION_KIND')
            check(size==tuple((n+2)//5 for n in images[0].size),'PROP_DERIVATION_KIND')
            out=[cleared(images[0].resize(size,Image.Resampling.LANCZOS))]
        else:
            out=pixelize(images,size)
        check((root/'source.png').read_bytes()==payload,'PROP_DERIVATION_INPUT_CHANGED')
        return encode(out,durations)
    paths=[root/'raw'/f'frame-{i:08d}.png' for i in range(len(spec['frames']))]
    original=[p.read_bytes() for p in paths]
    size=(spec['canvas']['width'],spec['canvas']['height'])
    duration=spec['frameDurationsMs'];kind=spec['kind']
    check(size[0]*size[1]*len(paths)<=MAX_PIXELS)
    check(kind=='detail','PROP_DERIVATION_KIND')
    for row,raw in zip(spec['frames'],original):
        check(len(raw)==row['bytes'] and hashlib.sha256(raw).hexdigest()==row['sha256'],'PROP_DERIVATION_INPUT_CHANGED')
        try:
            with Image.open(io.BytesIO(raw)) as png:
                check(png.format=='PNG' and png.size==(row['width'],row['height']) and png.n_frames==1)
                png.verify()
        except Exception:
            if not duration:raise ValueError('PROP_DERIVATION_STATIC_SOURCE_PNG_INVALID') from None
            raise
    if corridor:
        try:
            from prop_corridor import cutouts
            images=[cleared(fit(frame,size)) for frame in cutouts(root,spec)]
        except Exception:
            raise ValueError('PROP_DERIVATION_CORRIDOR_OUTPUT') from None
    elif not duration:
        check(len(paths)==1,'PROP_DERIVATION_STATIC_SOURCE_PNG_INVALID')
        try:
            native=trustworthy_static_rgba(original[0],size)
        except Exception:
            raise ValueError('PROP_DERIVATION_STATIC_SOURCE_PNG_INVALID') from None
        check(native is not None,'PROP_DERIVATION_STATIC_NATIVE_ALPHA_REQUIRED')
        images=[native]
    elif use_chroma:
        images=[]
        for source in original:
            try:
                with Image.open(io.BytesIO(source)) as im:
                    frame=connected_candle_chroma(im)
            except Exception:
                raise ValueError('PROP_DERIVATION_CHROMA_FRAME') from None
            images.append(cleared(fit(frame,size)))
    else:
        try:
            mattes=matte(root,paths,spec['sourceHash'])
        except Exception as error:
            key=error.args[0] if len(error.args)==1 and type(error.args[0]) is str else None
            raise ValueError({
                'alpha_no_subject_initial_mask':'PROP_DERIVATION_INITIAL_MASK_EMPTY',
                'alpha_tracker_frame_indices_mismatch':'PROP_DERIVATION_TRACKER_FRAME_SET',
            }.get(key,'PROP_DERIVATION_MATTE_BACKEND')) from None
        check(len(paths)==len(mattes))
        images=[]
        for row,path in zip(spec['frames'],mattes):
            with Image.open(path) as im:
                check(im.mode=='RGBA' and im.n_frames==1 and im.size==(row['width'],row['height']))
                images.append(cleared(fit(cleared(im.copy()),size)))
    check([p.read_bytes() for p in paths]==original,'PROP_DERIVATION_INPUT_CHANGED')
    return encode(images,duration)


def pinned_matte(root,paths,source_hash):
    import os
    check(len(paths)>=2,'PROP_DERIVATION_MATTE_BACKEND')
    from workers.resident.backends.frame_full_alpha_cli import run_alpha
    run_alpha(root,os.environ['WORKFLOW_POSTPROCESS_MODEL_DIGEST'],{'sourceSha256':source_hash})
    targets=sorted((root/'alpha').glob('frame-*.png'))
    for path in targets:
        with Image.open(path) as image:
            rgba=image.convert('RGBA')
        rgba.putalpha(rgba.getchannel('A').point(lambda n:0 if n<16 else n))
        rgba.save(path)
    return targets


_EXIT={
    'PROP_DERIVATION_OUTPUT_BUDGET':31,
    'PROP_DERIVATION_ALPHA_REQUIRED':32,
    'PROP_DERIVATION_MATTE_BACKEND':33,
    'PROP_DERIVATION_INITIAL_MASK_EMPTY':34,
    'PROP_DERIVATION_TRACKER_FRAME_SET':35,
    'PROP_DERIVATION_INPUT_CHANGED':36,
    'PROP_DERIVATION_APNG_ENCODE':37,
    'PROP_DERIVATION_PIXEL_FOREGROUND':38,
    'PROP_DERIVATION_STATIC_NATIVE_ALPHA_REQUIRED':39,
    'PROP_DERIVATION_STATIC_SOURCE_PNG_INVALID':40,
    'PROP_DERIVATION_CHROMA_FRAME':41,
    'PROP_DERIVATION_RENDER_PROFILE':42,
    'PROP_DERIVATION_CORRIDOR_OUTPUT':43,
}


if __name__=='__main__':
    try:
        root=Path(sys.argv[1]);spec=json.loads((root/'job.json').read_bytes())
        (root/'result.png').write_bytes(process(root,spec,pinned_matte,sys.argv[2:]==['corridor-v2']))
    except Exception as error:
        # No paths, model stderr, raw pixels, or stack traces on worker logs.
        c=error.args[0] if len(error.args)==1 and type(error.args[0]) is str else None
        sys.exit(_EXIT.get(c,1))
