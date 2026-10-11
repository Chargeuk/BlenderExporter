import importlib.util
from pathlib import Path
import tempfile
import unittest
import json
import numpy as np
from PIL import Image

spec=importlib.util.spec_from_file_location('appearance',Path(__file__).resolve().parents[1]/'src/babylon_js/workflow_tools/compare_appearance.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


class AppearanceTests(unittest.TestCase):
    def test_lab_reference_primaries(self):
        actual=m.lab(np.array([[0.,0,0],[1,1,1],[1,0,0],[0,1,0],[0,0,1]]))
        expected=[[0,0,0],[100,0,0],[53.2408,80.0925,67.2032],[87.7347,-86.1827,83.1793],[32.297,79.1875,-107.86]]
        np.testing.assert_allclose(actual,expected,atol=.002)

    def test_identity_brightness_and_neutral_hue(self):
        a=np.full((8,8,3),.5)
        c=m.components(a,a)
        for key in ('lightness','chroma','hue','delta_e76'):
            self.assertLess(np.abs(c[key]).max(),1e-9)
        c=m.components(a+.1,a)
        self.assertGreater(c['lightness'].min(),0)
        self.assertLess(c['hue'].max(),1e-5)

    def test_hue_wrap_and_chroma_sign(self):
        original=m.lab
        def fake(rgb):return rgb
        try:
            m.lab=fake
            def v(deg,c=30):return np.array([50,c*np.cos(np.radians(deg)),c*np.sin(np.radians(deg))])
            c=m.components(v(179),v(-179))
            self.assertAlmostEqual(float(c['hue_degrees']),-2)
            self.assertLess(float(c['hue']),1.1)
            self.assertGreater(float(m.components(v(0,40),v(0,20))['chroma']),0)
        finally:m.lab=original

    def test_exclusions_do_not_bleed_into_blur(self):
        a=np.ones((30,30)); valid=np.ones_like(a,dtype=bool)
        a[:,15:]=100; valid[:,15:]=False
        np.testing.assert_allclose(m.blur_masked(a,valid,3)[valid],1)

    def test_end_to_end_masks_fixed_scales_and_refusal(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)
            Image.new('RGB',(40,40),(128,128,128)).save(p/'target.png')
            Image.new('RGB',(40,40),(150,128,110)).save(p/'candidate.png')
            cfg={'size':[40,40],'regions':[{'name':'seat','family':'wood','polygon':[[2,2],[37,2],[37,37],[2,37]]}],
                 'exclude':[{'reason':'specular','polygon':[[2,2],[8,2],[8,37],[2,37]]}]}
            (p/'regions.json').write_text(json.dumps(cfg))
            args=m.parser().parse_args(['--candidate',str(p/'candidate.png'),'--target',str(p/'target.png'),
                                      '--baseline',str(p/'target.png'),'--regions',str(p/'regions.json'),
                                      '--output',str(p/'out'),'--assume-srgb'])
            r=m.run(args)
            self.assertEqual(r['baseline']['global']['delta_e76_mean'],0)
            self.assertGreater(r['candidate']['regions'][0]['delta_e76_mean'],0)
            self.assertEqual(r['settings']['scales_L_C_H_Y'],[20.,20.,20.,.2])
            self.assertTrue((p/'out/candidate/region_01.png').is_file())
            with self.assertRaises(ValueError):m.run(args)
            with self.assertRaises(ValueError):m.read_rgb(p/'target.png')
            cfg['size']=[41,40]
            with self.assertRaises(ValueError):m.masks(cfg,(40,40),3)
            Image.new('RGBA',(40,40),(0,0,0,0)).save(p/'alpha.png')
            with self.assertRaises(ValueError):m.read_rgb(p/'alpha.png',True)


if __name__=='__main__':unittest.main()
