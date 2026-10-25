"""Opt-in installed libktx integration against an EXISTING failed KTX; no encoder.

Set BJS_PLUGIN_ROOT, BJS_UASTC_SOURCE, BJS_UASTC_PREPARED, BJS_UASTC_OUTPUT,
BJS_KTX and BJS_RUN_UASTC_INTEGRATION=1. All outputs are retained, never retried.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import struct
import types
import unittest


@unittest.skipUnless(os.environ.get('BJS_RUN_UASTC_INTEGRATION')=='1','Explicit retained-output installed integration required')
class InstalledNativeRGBA(unittest.TestCase):
    def test_existing_Advanced_KTX_alpha_ceiling_RGB_byte_exact(self):
        root=Path(os.environ['BJS_PLUGIN_ROOT'])
        package=types.ModuleType('installed_alpha_test');package.__path__=[str(root)];sys.modules[package.__name__]=package
        spec=importlib.util.spec_from_file_location(package.__name__+'.uastc_alpha',root/'uastc_alpha.py')
        alpha=importlib.util.module_from_spec(spec);sys.modules[spec.name]=alpha;spec.loader.exec_module(alpha)
        source=Path(os.environ['BJS_UASTC_SOURCE']);prepared=Path(os.environ['BJS_UASTC_PREPARED']);output=Path(os.environ['BJS_UASTC_OUTPUT'])
        self.assertFalse(output.exists());output.mkdir(parents=True)
        original=hashlib.sha256(source.read_bytes()).hexdigest();prepared_hash=hashlib.sha256(prepared.read_bytes()).hexdigest()
        target=output/'Advanced_alpha_ceiling.ktx2'
        proof=alpha.enforce_source_ceiling(source,target,prepared=prepared,divisor=15,ktx=os.environ['BJS_KTX'])
        self.assertTrue(proof['RGB_byte_exact']);self.assertEqual(proof['alpha_above_source_ceiling'],0)
        self.assertTrue(proof['effective_divisor_constant']);self.assertEqual(proof['original_divisor_byte'],15)
        self.assertEqual(proof['minimum_divisor'],15/255);self.assertLessEqual(proof['alpha_after_range'][1],15)
        self.assertEqual(original,hashlib.sha256(source.read_bytes()).hexdigest())
        self.assertEqual(prepared_hash,hashlib.sha256(prepared.read_bytes()).hexdigest())
        # Malformed/sRGB variants are owned copies, never accepted inputs. They
        # must be rejected before libktx patch/decode. No encoder is involved.
        srgb=output/'rejected_srgb.ktx2';data=bytearray(source.read_bytes())
        dfdo=struct.unpack_from('<I',data,48)[0];data[dfdo+14]=2;srgb.write_bytes(data)
        with self.assertRaises(ValueError):alpha.enforce_source_ceiling(srgb,output/'must_not_exist_srgb.ktx2',prepared=prepared,divisor=15,ktx=os.environ['BJS_KTX'])
        self.assertFalse((output/'must_not_exist_srgb.ktx2').exists())
        import numpy as np
        import OpenImageIO as oiio
        values=alpha.read_fixed_preparation(prepared,15).copy();values[0,0,3]=16
        nonfixed=output/'rejected_nonfixed.png';writer=oiio.ImageOutput.create(str(nonfixed))
        self.assertTrue(writer.open(str(nonfixed),oiio.ImageSpec(values.shape[1],values.shape[0],4,oiio.UINT8)))
        self.assertTrue(writer.write_image(values));writer.close()
        with self.assertRaises(ValueError):alpha.enforce_source_ceiling(source,output/'must_not_exist_nonfixed.ktx2',prepared=nonfixed,divisor=15,ktx=os.environ['BJS_KTX'])
        self.assertFalse((output/'must_not_exist_nonfixed.ktx2').exists())
        (output/'INSTALLED_NATIVE_RGBA_INTEGRATION.json').write_text(json.dumps(dict(status='PASSED_NATIVE_RGBA_ONLY;HDR_CODE_SCREENS_NOT_WAIVED',
            proof=proof,source_hash_unchanged=True,prepared_hash_unchanged=True,encoder_calls=0,render_calls=0,runtime_tested=False),indent=2)+'\n')


if __name__=='__main__':unittest.main()
