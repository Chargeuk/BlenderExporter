"""Pure volume math/format regression checks; no Blender or capture execution."""
import copy
import importlib.util
from pathlib import Path
import sys
import types
import unittest
import numpy as np

ROOT=Path(__file__).resolve().parents[1]/'src/babylon_js'
package=types.ModuleType('volume_test_package');package.__path__=[str(ROOT)];sys.modules[package.__name__]=package
spec=importlib.util.spec_from_file_location(package.__name__+'.environment_lighting_volume',ROOT/'environment_lighting_volume.py')
volume=importlib.util.module_from_spec(spec);sys.modules[spec.name]=volume;spec.loader.exec_module(volume)


def receipt():
    return dict(version=1,dimensions=[32,16,32],atlasColumns=4,atlasRows=4,atlasLayout=volume.LAYOUT,
        bounds=dict(min=[-6,.15,-6],max=[6,3.4,6]),masterSha256='a'*64,flip_y=False,
        normalization=dict(method='solid-angle-rgb-mean-over-env-reference',referenceRgb=[1,2,3]))


class VolumeRegression(unittest.TestCase):
    def test_uniform_rgb_spherical_mean(self):
        pixels=np.ones((8,16,3),np.float32)*[2,.5,4]
        np.testing.assert_allclose(volume.panorama_mean(pixels),[2,.5,4])

    def test_solid_angle_poles_have_less_weight(self):
        pixels=np.zeros((4,8,3),np.float32);pixels[0]=1
        expected=(1-np.cos(np.pi/4))/2
        np.testing.assert_allclose(volume.panorama_mean(pixels),expected)

    def test_ratio_preserves_tint_and_hdr(self):
        pixels=np.ones((4,8,3),np.float32)*[8,2,1]
        np.testing.assert_allclose(volume.relative_rgb(pixels,[2,2,2]),[4,1,.5])

    def test_reference_near_zero_and_rgbd_overflow_reject(self):
        pixels=np.ones((2,4,3),np.float32)
        for ref in ([0,1,1],[1e-6,1,1],[float('nan'),1,1],[.001,.001,.001]):
            with self.assertRaises(ValueError):volume.relative_rgb(pixels,ref)

    def test_nonfinite_negative_and_partial_panorama_reject(self):
        for pixels in (np.ones((2,3,3)),np.full((2,4,3),-1),np.full((2,4,3),np.nan)):
            with self.assertRaises(ValueError):volume.panorama_mean(pixels)

    def test_raw_voxel_and_atlas_axes(self):
        grid=np.ones((32,16,32,4),np.float32)
        z,y,x=np.indices(grid.shape[:3]);grid[...,0]=x;grid[...,1]=y;grid[...,2]=z
        atlas=volume.pack_atlas(grid)
        for ix,iy,iz in ((0,0,0),(31,15,31),(7,5,12),(20,9,3)):
            np.testing.assert_array_equal(atlas[(iy//4)*32+iz,(iy%4)*32+ix],[ix,iy,iz,1])
            offset=((iz*16+iy)*32+ix)*4
            np.testing.assert_array_equal(grid.ravel()[offset:offset+4],[ix,iy,iz,1])

    def test_interpolation_is_centered_and_clamped(self):
        grid=np.ones((2,2,2,4),np.float32)
        grid[:,:,:,0]=[0,2]
        result=volume.resample_grid(grid,[4,2,2])
        np.testing.assert_allclose(result[0,0,:,0],[0,.5,1.5,2])
        np.testing.assert_array_equal(result[...,3],1)

    def test_uniform_neutral_interpolation_stays_neutral(self):
        result=volume.resample_grid(np.ones((8,4,8,4),np.float32))
        self.assertEqual(result.shape,(32,16,32,4));np.testing.assert_array_equal(result,1)

    def test_contract_and_source_derived_divisor(self):
        result=volume.payload(receipt(),'b'*64,.125)
        self.assertEqual(result['texture'],'environment_lighting_volume_rgbd.ktx2')
        self.assertEqual(result['minDivisor'],.125);self.assertEqual(result['strength'],1)
        self.assertEqual(result['outsideBounds'],'neutral')
        self.assertEqual(result['sampling'],'texel-centered-trilinear')

    def test_layout_flip_and_bounds_guards(self):
        for key,value in (('dimensions',[32,32,16]),('atlasColumns',8),('atlasLayout','other'),('flip_y',True)):
            record=receipt();record[key]=value
            with self.assertRaises(ValueError):volume.validate_record(record)
        record=receipt();record['bounds']['max'][0]=-6
        with self.assertRaises(ValueError):volume.validate_record(record)

    def test_wrong_atlas_shape_rejects(self):
        with self.assertRaises(ValueError):volume.pack_atlas(np.ones((16,32,32,4),np.float32))

    def test_bad_metadata_data_rejects(self):
        for digest,divisor,strength in (('x'*64,1,1),('b'*64,0,1),('b'*64,1,-1)):
            with self.assertRaises(ValueError):volume.payload(copy.deepcopy(receipt()),digest,divisor,strength)

    def test_saved_world_association_defaults_off(self):
        self.assertIsNone(volume.saved_options({}))
        result=volume.saved_options(dict(bjs_environment_lighting_volume_config='//receipt.json'))
        self.assertEqual(result,dict(config='//receipt.json',image='',strength=1))

    def test_metadata_attaches_only_to_existing_env_marker(self):
        model=dict(metadata=dict(existing='scene'),meshes=[dict(name='hasenv',metadata=dict(existing='marker'))],lights=[dict(name='sun')])
        volume.marker_metadata(model)[volume.KEY]=dict(version=1)
        self.assertNotIn(volume.KEY,model['metadata']);self.assertNotIn('metadata',model['lights'][0])
        self.assertEqual(model['meshes'][0]['metadata']['existing'],'marker')
        for other in ({'meshes':[]},{'meshes':[dict(name='hasenv',positions=[0,0,0])]}, {'meshes':[dict(name='hasenv'),dict(name='hasenv')]}):
            with self.assertRaises(ValueError):volume.marker_metadata(other)


if __name__=='__main__':unittest.main()
