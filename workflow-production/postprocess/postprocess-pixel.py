"""Deterministic 128px fit, then logical-pixel cleanup for a static PNG.

Settings and area/weighted-median-cut policy follow the NGS generic normalizer
(pixel-normalize.integer-area-median-cut.v1), not the 384px animation-input profile.
No generator, upscaler, background inference, or mutation of the source.
"""
import io
import sys
from collections import Counter
from pathlib import Path
from PIL import Image

SIZE=128
PADDING=1
ALPHA=128
COLORS=32
MAX_PIXELS=4_194_304

def resize_candidate(image):
    sw,sh=image.size
    if max(sw,sh)<=SIZE:
        raise ValueError('POSTPROCESS_STATIC_PIXEL_NOT_REDUCED')
    source=list(image.get_flattened_data()) if hasattr(image,'get_flattened_data') else list(image.getdata())
    if sum(a<ALPHA for _,_,_,a in source)*100<sw*sh:
        raise ValueError('POSTPROCESS_STATIC_PIXEL_ALPHA_REQUIRED')
    aw=SIZE-2*PADDING
    if sw>=sh:
        dw,dh=aw,max(1,(sh*aw+sw//2)//sw)
    else:
        dw,dh=max(1,(sw*aw+sh//2)//sh),aw
    ox,oy=(SIZE-dw)//2,(SIZE-dh)//2
    output=[(0,0,0,0)]*(SIZE*SIZE)
    area=sw*sh
    # Exact area overlap; premultiply RGB by actual source alpha to exclude matte bleed.
    xs=[[(sx,min((sx+1)*dw,(x+1)*sw)-max(sx*dw,x*sw))
        for sx in range(x*sw//dw,((x+1)*sw+dw-1)//dw)] for x in range(dw)]
    for y in range(dh):
        yr=[(sy*sw,min((sy+1)*dh,(y+1)*sh)-max(sy*dh,y*sh))
            for sy in range(y*sh//dh,((y+1)*sh+dh-1)//dh)]
        for x,xr in enumerate(xs):
            r=g=b=a=0
            for row,wy in yr:
                for sx,wx in xr:
                    pr,pg,pb,pa=source[row+sx]
                    weight=wx*wy*pa
                    r+=pr*weight;g+=pg*weight;b+=pb*weight;a+=weight
            if a>=ALPHA*area:
                output[(y+oy)*SIZE+x+ox]=((r+a//2)//a,(g+a//2)//a,(b+a//2)//a,255)
    return output

def palette_for(hist):
    boxes=[sorted(hist)]
    while len(boxes)<COLORS:
        candidates=[]
        for i,box in enumerate(boxes):
            spans=[max(c[k] for c in box)-min(c[k] for c in box) for k in range(3)]
            axis=max(range(3),key=lambda k:(spans[k],-k))
            if len(box)>1:candidates.append((spans[axis]*sum(hist[c] for c in box),-i,axis))
        if not candidates:break
        _,ni,axis=max(candidates)
        i=-ni;box=sorted(boxes[i],key=lambda c:(c[axis],c))
        half=(sum(hist[c] for c in box)+1)//2;acc=0
        for j,c in enumerate(box[:-1]):
            acc+=hist[c]
            if acc>=half:
                cut=j+1;break
        else:cut=len(box)-1
        boxes[i:i+1]=[box[:cut],box[cut:]]
    return sorted(set(tuple((sum(c[k]*hist[c] for c in box)+sum(hist[c] for c in box)//2)
                            //sum(hist[c] for c in box) for k in range(3)) for box in boxes))

def correct_pixels(reduced):
    hist=Counter(p[:3] for p in reduced if p[3])
    opaque=sum(hist.values())
    if not 16<=opaque<len(reduced):
        raise ValueError('POSTPROCESS_STATIC_PIXEL_FOREGROUND_INVALID')
    palette=palette_for(hist)
    mapping={c:min(palette,key=lambda q:(sum((c[k]-q[k])**2 for k in range(3)),q)) for c in hist}
    # One logical pixel = one palette color. No dithering or erasing isolated details.
    return [(*mapping[p[:3]],255) if p[3] else (0,0,0,0) for p in reduced]

def process(raw):
    with Image.open(io.BytesIO(raw)) as png:
        if png.format!='PNG' or getattr(png,'n_frames',1)!=1 or png.width*png.height>MAX_PIXELS:
            raise ValueError('POSTPROCESS_STATIC_PIXEL_SOURCE_INVALID')
        png.verify()
    with Image.open(io.BytesIO(raw)) as png:
        reduced=resize_candidate(png.convert('RGBA'))
    output=Image.new('RGBA',(SIZE,SIZE));output.putdata(correct_pixels(reduced))
    data=io.BytesIO();output.save(data,format='PNG',optimize=False)
    return data.getvalue()



def process_background(raw,source_sha):
    """Full-canvas background correction. Never sample, resize or blur output coordinates."""
    import re
    from functools import lru_cache
    from PIL.PngImagePlugin import PngInfo
    if not re.fullmatch('[a-f0-9]{64}',source_sha):
        raise ValueError('BACKGROUND_PIXEL_SOURCE_PIN')
    with Image.open(io.BytesIO(raw)) as src:
        if (src.format,src.size,getattr(src,'n_frames',1))!=('PNG',(1536,1024),1):
            raise ValueError('BACKGROUND_PIXEL_SOURCE_INVALID')
        src.load()
        if src.convert('RGBA').getchannel('A').getextrema()!=(255,255):
            raise ValueError('BACKGROUND_PIXEL_SOURCE_NOT_OPAQUE')
        original=src.convert('RGB')
    # A strided training sample defines the palette only. Every output pixel
    # still comes from the matching original coordinate, without interpolation.
    sampled=Counter(original.getpixel((x,y)) for y in range(0,1024,3)
                    for x in range(y%5,1536,5))
    palette=palette_for(sampled)
    @lru_cache(maxsize=65536)
    def nearest(c):
        return min(palette,key=lambda q:(sum((u-v)**2 for u,v in zip(c,q)),q))
    output=Image.new('RGB',original.size)
    output.putdata([nearest(x) for x in original.getdata()])
    metadata=PngInfo();metadata.add_text('BackgroundPixelSourceSHA256',source_sha)
    stream=io.BytesIO();output.save(stream,format='PNG',pnginfo=metadata)
    return stream.getvalue()


if __name__=='__main__':
    root=Path(sys.argv[1])
    raw=(root/'source.png').read_bytes()
    result=process_background(raw,sys.argv[3]) if len(sys.argv)==4 and sys.argv[2]=='background' else process(raw)
    (root/'pixel.png').write_bytes(result)
