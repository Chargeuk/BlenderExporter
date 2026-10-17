"""Focused NumPy RGBD region regression; run with unittest (SciPy parity optional)."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
import numpy as np

MODULE=Path(__file__).resolve().parents[1]/'src/babylon_js/ktx_conversion.py'
spec=importlib.util.spec_from_file_location('region_conversion',MODULE)
core=importlib.util.module_from_spec(spec);spec.loader.exec_module(core)


class RegionRGBDTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.labels=np.zeros((16,16),np.int32)
        self.labels[1:3,1:3]=1;self.labels[8:11,8:11]=2
        self.labels[2,3]=3  # owned neighbour protects the complete first codec block
        self.rgb=np.random.default_rng(7).uniform(0,12,(16,16,3)).astype(np.float32)
        self.original,_=core.encode_rgbd(self.rgb)
        np.save(self.root/'labels.npy',self.labels)
        self.islands=[dict(id=1,object='stepped fixture'),dict(id=2,object='fixture rose'),dict(id=3,object='protected')]
        (self.root/'islands.json').write_text(json.dumps(self.islands))
        self.policy=dict(schema_version=1,encoding='rgbd-v1',ownership_labels='labels.npy',islands='islands.json',
            selection=dict(objects=['stepped fixture','fixture rose']),stepped_selection=dict(objects=['stepped fixture']),
            block_size=4,guard_pixels=1,minimum_alpha_code=3,alpha_steps=[3,7,15,31,63,127,255])
        self.save_policy()

    def tearDown(self):self.temp.cleanup()
    def save_policy(self):
        (self.root/'policy.json').write_text(json.dumps(self.policy))
        self.bound=core.load_region_policy(self.root/'policy.json')
    def encode(self):return core.encode_rgbd_regions(self.rgb,self.original,self.bound)

    def test_blocks_mixed_protection_and_exact_outside(self):
        encoded,footprint,metrics=self.encode()
        self.assertGreater(metrics['rgbd_region']['affected_blocks'],0)
        self.assertGreater(metrics['rgbd_region']['mixed_blocks_retained_original'],0)
        self.assertFalse(footprint[:4,:4].any())
        self.assertTrue(np.array_equal(encoded[~footprint],self.original[~footprint]))
        self.assertFalse(footprint[self.labels==3].any())
        self.assertEqual(footprint.reshape(4,4,4,4).all(axis=(1,3)).sum(),metrics['rgbd_region']['affected_blocks'])
        for y,x in zip(*np.where(footprint.reshape(4,4,4,4).any(axis=(1,3)))):
            self.assertEqual(len(np.unique(encoded[y*4:y*4+4,x*4:x*4+4,3])),1)

    def test_legacy_fixture_semantics_match_scipy(self):
        try:from scipy.ndimage import distance_transform_edt
        except ImportError:self.skipTest('SciPy only needed for legacy reference parity')
        distance,indices=distance_transform_edt(self.labels<=0,return_indices=True)
        guard=np.where(distance<=1,self.labels[tuple(indices)],0)
        def blocks(mask):return mask.reshape(4,4,4,4).any(axis=(1,3))
        special=blocks(guard==1);selected=blocks(np.isin(guard,[1,2]))
        affected=selected & ~blocks((self.labels>0)&~np.isin(self.labels,[1,2]))
        safe=np.minimum(np.floor(255/np.maximum(self.rgb.reshape(4,4,4,4,3).max(axis=(1,3,4)),1e-7)),255).astype(np.uint8)
        steps=np.array([3,7,15,31,63,127,255],np.uint8)
        codes=safe.copy();codes[special]=steps[np.searchsorted(steps,safe[special],side='right')-1]
        alpha=codes.repeat(4,0).repeat(4,1)
        scaled=self.rgb*(alpha.astype(np.float32)/255)[...,None]
        candidate=np.dstack((np.rint(np.power(scaled,1/2.2)*255).astype(np.uint8),alpha))
        expected=self.original.copy();footprint=affected.repeat(4,0).repeat(4,1);expected[footprint]=candidate[footprint]
        actual,selected,_=self.encode()
        self.assertTrue(np.array_equal(footprint,selected))
        self.assertTrue(np.array_equal(expected,actual))

    def test_ownership_downsample_and_flip_artifacts(self):
        labels=self.labels.repeat(2,0).repeat(2,1);np.save(self.root/'labels.npy',labels);self.save_policy()
        encoded,footprint,_=self.encode()
        written={}
        metrics=core.region_artifacts(self.original,footprint,dict(output='test.ktx2',flip_y=True),self.root,
                                      lambda path,array:written.update({path.name:array}))
        self.assertTrue(np.array_equal(written['test_region_baseline.png'],self.original[::-1]))
        self.assertTrue(np.array_equal(np.load(metrics['rgbd_region_footprint']),footprint[::-1]))

    def test_reject_changed_bound_inputs_and_optimized_guards(self):
        np.save(self.root/'labels.npy',np.zeros_like(self.labels))
        with self.assertRaisesRegex(ValueError,'Region input changed'):self.encode()

    def test_reject_empty_unknown_and_outside_stepped_selection(self):
        for selected,stepped in [(dict(ids=[999]),None),(dict(ids=[1]),dict(ids=[2]))]:
            with self.subTest(selected=selected):
                self.policy['selection']=selected;self.policy['stepped_selection']=stepped;self.save_policy()
                with self.assertRaises(ValueError):self.encode()

    def test_reject_nonfinite_and_selected_radiance_clipping(self):
        self.rgb[8,8,0]=np.nan
        with self.assertRaises(ValueError):self.encode()
        self.rgb[8,8,0]=100
        with self.assertRaisesRegex(ValueError,'minimum divisor'):self.encode()

    def test_reject_invalid_shape_steps_and_ownership(self):
        self.policy['alpha_steps']=[3,3,255];self.save_policy()
        with self.assertRaises(ValueError):self.encode()
        self.policy['alpha_steps']=[3,7,15,31,63,127,255]
        labels=self.labels.astype(np.float32);labels[0,0]=.5;np.save(self.root/'labels.npy',labels);self.save_policy()
        with self.assertRaisesRegex(ValueError,'integer IDs'):self.encode()

    def test_default_encoder_unchanged_and_independent_colour_codec(self):
        rgb=np.array([[[0,0,0],[1,1,1],[255,255,255]]],np.float32)
        pixels,_=core.encode_rgbd(rgb)
        self.assertEqual(pixels.tolist(),[[[0,0,0,255],[255,255,255,255],[255,255,255,1]]])
        self.assertEqual(core.colour_codec(dict(codec='basis-lz')),'basis-lz')
        self.assertEqual(core.colour_codec(dict(codec='uastc',colour_adjustment_codec='INHERIT')),'uastc')
        self.assertEqual(core.colour_codec(dict(codec='basis-lz',colour_adjustment_codec='uastc')),'uastc')
        with self.assertRaises(ValueError):core.colour_codec(dict(colour_adjustment_codec='invalid'))


if __name__=='__main__':unittest.main()
