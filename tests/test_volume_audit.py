"""Small numeric contracts for volume axes, neutral bounds, and distinct lookup orders."""
import importlib.util
from pathlib import Path
import sys
import unittest
import numpy as np

ROOT=Path(__file__).resolve().parents[1]/'src/babylon_js/workflow_tools'
sys.path.insert(0,str(ROOT))
spec=importlib.util.spec_from_file_location('tested_volume_audit',ROOT/'volume_audit.py')
audit=importlib.util.module_from_spec(spec);spec.loader.exec_module(audit)

class VolumeAuditSamplingTests(unittest.TestCase):
    def test_asymmetric_atlas_banks_and_axes(self):
        atlas=np.zeros((768,128,4),np.float32)
        for bank in range(6):
            for y in range(16):
                row,col=divmod(y,4)
                for z in range(32):
                    atlas[bank*128+row*32+z,col*32:col*32+32,:3]=np.stack((np.arange(32),np.full(32,y),np.full(32,z)),axis=-1)+bank*100
        result=audit.fields(atlas,6)
        np.testing.assert_array_equal(result[4,7,5,3,:3],[403,405,407])
    def test_texel_centers_clamp_edges_and_outside_is_neutral(self):
        field=np.ones((2,2,2,3));field[:,:,:,0]=[2,6]
        points=np.array([[.25,.25,.25],[.75,.75,.75],[.5,.5,.5],[0,0,0],[1,1,1],[-.01,.5,.5]])
        np.testing.assert_allclose(audit.trilinear(field,points)[:,0],[2,6,4,2,6,1])
    def test_encoded_filter_first_is_reported_separately_from_frozen_voxel_first(self):
        encoded=np.ones((2,2,2,4));encoded[:,:,:,0]=[.1,1]
        points=np.array([[.5,.5,.5]])
        encoded_value=audit.encoded_first(encoded,points,1)[0,0]
        voxel_value=audit.trilinear(encoded[...,:3]**2.2,points)[0,0]
        self.assertAlmostEqual(encoded_value,.55**2.2)
        self.assertNotAlmostEqual(encoded_value,voxel_value)
    def test_invalid_lookup_coordinates_are_hard_failures(self):
        with self.assertRaises(ValueError):audit.trilinear(np.ones((2,2,2,3)),np.array([[np.nan,0,0]]))
    def test_normalized_error_preserves_hdr_and_zero_reference(self):
        np.testing.assert_allclose(audit.norm_error(np.array([[2.,0,0],[.1,0,0]]),np.array([[4.,0,0],[0.,0,0]])),[.5,.1])

if __name__=='__main__':unittest.main()
