import importlib.util,io,unittest
from pathlib import Path
from PIL import Image
S=importlib.util.spec_from_file_location('static_pixel',Path(__file__).with_name('postprocess-pixel.py'))
m=importlib.util.module_from_spec(S);S.loader.exec_module(m)

def png(image):
    buf=io.BytesIO();image.save(buf,format='PNG');return buf.getvalue()

class Tests(unittest.TestCase):
    def test_fit_then_distinct_color_and_alpha_cleanup_preserve_raw(self):
        src=Image.new('RGBA',(256,192),(250,0,250,0))
        src.paste((10,20,30,180),(20,20,230,172))
        for y in range(28,164):
            for x in range(30,226):
                src.putpixel((x,y),(x%256,y%256,(x+y)%256,255))
        raw=png(src);reduced=m.resize_candidate(src)
        self.assertGreater(len({p[:3] for p in reduced if p[3]}),32)
        self.assertEqual(m.process(raw),m.process(raw))
        output=m.process(raw);self.assertNotEqual(raw,output)
        with Image.open(io.BytesIO(output)) as result:
            self.assertEqual(result.mode,'RGBA');self.assertEqual(result.size,(128,128))
            colors=set(result.getdata())
            self.assertEqual({p[3] for p in colors},{0,255})
            self.assertLessEqual(len({p[:3] for p in colors if p[3]}),32)
            self.assertEqual({p for p in colors if not p[3]},{(0,0,0,0)})
            left,top,right,bottom=result.getchannel('A').getbbox()
            self.assertTrue(0<left<right<128 and 0<top<bottom<128)
            self.assertGreater(top,left)
        self.assertEqual(raw,png(src))
    def test_transparent_rgb_never_bleeds(self):
        a=Image.new('RGBA',(256,256),(255,0,255,0))
        b=Image.new('RGBA',(256,256),(0,255,0,0))
        for im in (a,b):
            im.paste((21,27,40,255),(45,35,207,241))
        self.assertEqual(m.process(png(a)),m.process(png(b)))
    def test_reduction_and_real_alpha_required(self):
        for img,code in ((Image.new('RGBA',(128,128)), 'NOT_REDUCED'),
                         (Image.new('RGBA',(256,256),(31,29,7,255)),'ALPHA_REQUIRED'),
                         (Image.new('RGBA',(256,256),(31,29,7,0)),'FOREGROUND_INVALID')):
            with self.subTest(code=code),self.assertRaisesRegex(ValueError,code):m.process(png(img))
    def test_animated_png_and_non_png_rejected(self):
        im=Image.new('RGBA',(256,256));buf=io.BytesIO()
        im.save(buf,format='PNG',save_all=True,append_images=[Image.new('RGBA',im.size,(223,0,23,255))],duration=100,loop=0)
        with self.assertRaisesRegex(ValueError,'SOURCE_INVALID'):m.process(buf.getvalue())
        buf=io.BytesIO();im.convert('RGB').save(buf,format='JPEG')
        with self.assertRaisesRegex(ValueError,'SOURCE_INVALID'):m.process(buf.getvalue())
if __name__=='__main__':unittest.main()
