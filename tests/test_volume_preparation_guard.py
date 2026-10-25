"""The actual volume producer uses this pure whole-master PNG byte boundary before encoding."""
import hashlib
import importlib.util
from pathlib import Path
import sys
import tempfile
import types
import unittest
import numpy as np

ROOT=Path(__file__).resolve().parents[1]/'src/babylon_js'
package=types.ModuleType('volume_png_guard_package');package.__path__=[str(ROOT)];sys.modules[package.__name__]=package
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module);return module
core=load(package.__name__+'.ktx_conversion',ROOT/'ktx_conversion.py')
volume=load(package.__name__+'.environment_lighting_volume',ROOT/'environment_lighting_volume.py')

class VolumePreparedByteGuardTests(unittest.TestCase):
    def test_exact_preparation_for_simple_block4_and_fixed_advanced(self):
        master=np.linspace(0,7,8*8*3,dtype=np.float32).reshape(8,8,3)
        for mode,fixed in (('per-pixel',False),('block-4x4',False),('per-pixel',True)):
            expected,_=core.encode_rgbd(master,mode,fixed_divisor=fixed)
            self.assertEqual(volume.verify_prepared_volume_rgba(master,expected,mode,fixed_divisor=fixed),hashlib.sha256(expected.tobytes()).hexdigest())
    def test_changed_png_channel_or_divisor_is_rejected(self):
        master=np.ones((4,4,3),np.float32)*2;expected,_=core.encode_rgbd(master,'block-4x4')
        for channel in range(4):
            changed=expected.copy();changed[0,0,channel]^=1
            with self.assertRaises(ValueError):volume.verify_prepared_volume_rgba(master,changed,'block-4x4')
    def test_missing_or_float_png_is_rejected(self):
        master=np.ones((4,4,3),np.float32);expected,_=core.encode_rgbd(master)
        for value in (None,expected.astype(float)):
            with self.assertRaises(ValueError):volume.verify_prepared_volume_rgba(master,value)


    def test_actual_png_retains_low_divisor_unassociated_rgb_bytes(self):
        from PIL import Image
        master=np.empty((4,4,3),np.float32);master[:]=[2,.5,1]
        expected,_=core.encode_rgbd(master,'block-4x4')
        self.assertEqual(int(expected[...,3].min()),127)
        self.assertGreater(int(expected[0,0,0]),int(expected[0,0,3]))
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'prepared.png';Image.fromarray(expected,'RGBA').save(path)
            actual=volume.read_prepared_volume_rgba(path)
        np.testing.assert_array_equal(actual,expected)
        self.assertEqual(volume.verify_prepared_volume_rgba(master,actual,'block-4x4'),hashlib.sha256(expected.tobytes()).hexdigest())

    def test_actual_reader_rejects_missing_non_png_and_rgb_png(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            invalid=root/'invalid.png';invalid.write_bytes(b'not a PNG')
            rgb=root/'rgb.png';Image.fromarray(np.zeros((4,4,3),np.uint8),'RGB').save(rgb)
            for path in (root/'missing.png',invalid,rgb):
                with self.subTest(path=path.name),self.assertRaisesRegex(ValueError,'PNG'):
                    volume.read_prepared_volume_rgba(path)

    def test_actual_reader_rejects_truncated_rgba_png_payload(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'truncated.png'
            Image.fromarray(np.full((4,4,4),127,np.uint8),'RGBA').save(path)
            path.write_bytes(path.read_bytes()[:29])
            with self.assertRaisesRegex(ValueError,'PNG'):
                volume.read_prepared_volume_rgba(path)

if __name__=='__main__':unittest.main()
