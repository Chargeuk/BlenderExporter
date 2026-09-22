import importlib.util,unittest,numpy as np
from pathlib import Path
s=importlib.util.spec_from_file_location('core',Path(__file__).resolve().parents[1]/'src/babylon_js/ktx_conversion.py');c=importlib.util.module_from_spec(s);s.loader.exec_module(c)
class ResizeTests(unittest.TestCase):
 def test_linear_before_rgbd(self):
  a=np.zeros((1024,1024,3),np.float32);a[::2,::2]=64
  b=c.resize_lightmap(a,{'size':512,'color_space':'linear'},True)
  np.testing.assert_array_equal(b,np.full((512,512,3),16,np.float32))
  p,m=c.encode_rgbd(b);v=(p[...,:3]/255.)**2.2/(p[...,3:4]/255.)
  self.assertLess(np.abs(v-b).max(),.1);self.assertGreater(m['alpha_min'],0)
 def test_srgb_linear_average(self):
  a=np.zeros((1024,1024,4),np.float32);a[::2,:,:3]=1;a[...,3]=1
  b=c.resize_lightmap(a,{'size':512,'color_space':'srgb'},False)
  self.assertAlmostEqual(float(b[0,0,0]),.73535698,places=6);self.assertEqual(b[0,0,3],1)
 def test_preserve_and_no_upscale(self):
  a=np.ones((32,32,4),np.float32)
  for size in (0,512,1024,2048):self.assertIs(c.resize_lightmap(a,{'size':size,'color_space':'linear'},True),a)
 def test_invalid(self):
  for value in (True,'1024',256,-1,None):
   with self.assertRaises(ValueError):c.lightmap_size(value)
  with self.assertRaises(ValueError):c.resize_lightmap(np.ones((1024,512,3)),{'size':512},True)
 def test_arbitrary_box_integral(self):
  a=np.ones((513,513,4),np.float32)*3
  np.testing.assert_allclose(c.resize_lightmap(a,{'size':512,'color_space':'linear'},True),3,atol=1e-6)
if __name__=='__main__':unittest.main()
