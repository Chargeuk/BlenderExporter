"""Bounded regression tests; load pure helpers without importing Blender package init."""
import copy
import importlib.util
from pathlib import Path
import sys
import types
import unittest
import numpy as np

ROOT=Path(sys.argv[1]) if len(sys.argv)>1 else Path(__file__).parent/'babylon_js'
sys.argv=sys.argv[:1]
package=types.ModuleType('spatial_test_package');package.__path__=[str(ROOT)];sys.modules[package.__name__]=package
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);obj=importlib.util.module_from_spec(spec);sys.modules[name]=obj;spec.loader.exec_module(obj);return obj
spatial=module('spatial_test_package.spatial_lighting',ROOT/'spatial_lighting.py')
core=module('spatial_test_package.ktx_conversion',ROOT/'ktx_conversion.py')
capture=module('spatial_capture_test',ROOT/'environment_tools/spatial_capture.py')

def receipt():
    return dict(version=1,semantics='irradiance-multiplier',masterSha256='a'*64,
        bounds=dict(xMin=-6,xMax=6,zMin=-6,zMax=6),referenceHeight=1.,
        orientation='blender-xy-top-left-positive-y;babylon-xz-v-positive-z',
        normalization=dict(method='reference-luminance-rec709',referencePosition=[-2.4,1.,-1.2],measuredScalar=.5))

class Regression(unittest.TestCase):
    def test_surface_contract_has_no_fixed_height(self):
        r=receipt();r.pop('referenceHeight');r['captureMethod']='authored-surface-direct'
        r['normalization']=dict(method='covered-global-minmax-rec709',blackLuminance=0,globalRgbPeak=53.8733,coverageThreshold=.999,uncoveredInside='black')
        p=spatial.payload(r,'field.ktx2','b'*64,min_divisor=1)
        self.assertEqual(p['captureMethod'],'authored-surface-direct');self.assertNotIn('referenceHeight',p)
        self.assertNotIn('referencePosition',p['normalization']);self.assertEqual(p['minDivisor'],1)
    def test_surface_normalization_guard(self):
        r=receipt();r['captureMethod']='authored-surface-direct'
        with self.assertRaises(ValueError):spatial.validate_record(r)
    def test_minmax_preserves_ratios_and_coverage(self):
        a=np.ones((2,2,4),np.float32);a[0,0,:3]=0;a[0,1,:3]=[4,2,1];a[1,0,:3]=[1,2,1];a[1,1,:]=[100,100,100,0]
        out,norm,mask=capture.covered_global_minmax(a)
        self.assertEqual(norm['blackLuminance'],0);self.assertEqual(norm['globalRgbPeak'],4)
        self.assertTrue(np.array_equal(out[0,1,:3],[1,.5,.25]));self.assertTrue(np.array_equal(out[1,1,:3],[0,0,0]))
        self.assertTrue(np.all(out[...,3]==1));self.assertEqual(int(mask.sum()),3)
    def test_minmax_removes_nonzero_black_with_common_factor(self):
        a=np.ones((1,2,4),np.float32);a[0,0,:3]=[.1,.1,.1];a[0,1,:3]=[2,1,.5]
        out,norm,_=capture.covered_global_minmax(a)
        self.assertTrue(np.all(out[0,0,:3]==0));self.assertAlmostEqual(float(out[0,1,0]/out[0,1,1]),2)
        self.assertAlmostEqual(float(out[0,1,1]/out[0,1,2]),2);self.assertEqual(float(out[0,1,:3].max()),1)
    def test_minmax_empty_or_zero_guard(self):
        for a in (np.zeros((2,2,4),np.float32),np.dstack((np.zeros((2,2,3),np.float32),np.ones((2,2),np.float32)))):
            with self.assertRaises(ValueError):capture.covered_global_minmax(a)
    def test_bounded_master_rgbd_divisor_is_one(self):
        pixels,metrics=core.encode_rgbd(np.array([[[0,.5,1]]],np.float32))
        self.assertEqual(metrics['alpha_min'],255);self.assertTrue(np.all(pixels[...,3]==255))
    def test_contract(self):
        p=spatial.payload(receipt(),'field.ktx2','b'*64,.7,.25)
        self.assertEqual(p['version'],1);self.assertEqual(p['minDivisor'],.25)
        self.assertEqual(p['uvMapping'],'unrotated-xz-v1');self.assertEqual(p['coordinateSpace'],'environment-local')
    def test_version_guard(self):
        r=receipt();r['version']=2
        with self.assertRaises(ValueError):spatial.validate_record(r)
    def test_zero_area(self):
        r=receipt();r['bounds']['xMax']=-6
        with self.assertRaises(ValueError):spatial.validate_record(r)
    def test_reference_scalar_guard(self):
        for value in (0,-1,float('nan'),float('inf')):
            r=receipt();r['normalization']['measuredScalar']=value
            with self.assertRaises(ValueError):spatial.validate_record(r)
    def test_output_traversal(self):
        with self.assertRaises(ValueError):spatial.payload(receipt(),'../field.ktx2','b'*64)
    def test_optin_default_off(self):
        obj=types.SimpleNamespace(type='LIGHT',name='sun',data=types.SimpleNamespace())
        self.assertEqual(spatial.opted_lights({'lights':[dict(name='sun')]},[obj]),[])
    def test_only_exported_optins(self):
        a=types.SimpleNamespace(type='LIGHT',name='a',data=types.SimpleNamespace(bjs_spatial_lighting=True))
        b=types.SimpleNamespace(type='LIGHT',name='b',data=types.SimpleNamespace(bjs_spatial_lighting=False))
        model={'lights':[dict(name='a',metadata={'existing':'preserve'}),dict(name='b')]}
        result=spatial.opted_lights(model,[a,b]);self.assertEqual([x[0]['name'] for x in result],['a'])
        self.assertEqual(result[0][0]['metadata'],{'existing':'preserve'})
    def test_duplicate_metadata_rejected(self):
        a=types.SimpleNamespace(type='LIGHT',name='a',data=types.SimpleNamespace(bjs_spatial_lighting=True))
        with self.assertRaises(ValueError):spatial.opted_lights({'lights':[dict(name='a',metadata={spatial.KEY:{}})]},[a])
    def test_scalar_normalization_preserves_cast(self):
        a=np.ones((4,4,4),np.float32);a[...,:3]=[2.,1.,.5]
        result,scalar,sample=capture.normalize(a,[0,0,1],[-1,1,-1,1])
        self.assertAlmostEqual(float(result[0,0,0]/result[0,0,1]),2.)
        self.assertAlmostEqual(float(np.dot(result[0,0,:3],[.2126,.7152,.0722])),1.,places=6)
    def test_coordinate_orientation(self):
        a=np.zeros((4,4,3),np.float32)
        for row in range(4):
            for col in range(4):a[row,col]=[col,3-row,0]
        self.assertGreater(capture.bilinear(a,[.75,0],[-1,1,-1,1])[0],capture.bilinear(a,[-.75,0],[-1,1,-1,1])[0])
        self.assertGreater(capture.bilinear(a,[0,.75],[-1,1,-1,1])[1],capture.bilinear(a,[0,-.75],[-1,1,-1,1])[1])
    def test_normalized_range_guard(self):
        for rgb in (-1.,float('nan'),0.):
            a=np.ones((2,2,4),np.float32);a[...,:3]=rgb
            with self.assertRaises(ValueError):capture.normalize(a,[0,0,1],[-1,1,-1,1])
    def test_rgbd_bound_is_source_derived(self):
        _,metrics=core.encode_rgbd(np.ones((4,4,3),np.float32)*8)
        self.assertGreater(metrics['alpha_min']/255,1/255)
        self.assertEqual(spatial.payload(receipt(),'map.ktx2','b'*64,min_divisor=metrics['alpha_min']/255)['minDivisor'],metrics['alpha_min']/255)

if __name__=='__main__':unittest.main()
