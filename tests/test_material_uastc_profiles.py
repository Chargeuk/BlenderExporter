"""CPU metadata regressions plus actual exporter-file smoke; no scene mutation."""
import argparse
import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import sys
import tempfile
import traceback
import unittest

parser=argparse.ArgumentParser()
parser.add_argument('--addon',type=Path,required=True)
parser.add_argument('--output',type=Path,required=True)
parser.add_argument('--actual-report',type=Path,required=True)
parser.add_argument('--source-blend',type=Path,action='append',default=[])
args=parser.parse_args()
assert not args.output.exists()
args.output.mkdir(parents=True)
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
before={str(p):sha(p) for p in args.source_blend}
helper=args.addon/'workflow_tools/decoded_delivery.py'
spec=importlib.util.spec_from_file_location('material_uastc_profile_under_test',helper)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

def fixture(path,*,model=163,transfer=2,compression=1,levels=1,alpha=False,primaries=1,premult=False):
    """Synthetic container structural metadata only; not a decodable codec payload."""
    offset=80+24*levels
    dfd=bytearray(44)
    struct.pack_into('<I',dfd,0,44)
    struct.pack_into('<IHH',dfd,4,0,2,40)
    struct.pack_into('<4B',dfd,12,model,primaries,transfer,1 if premult else 0)
    dfd[31]=(3 if model==166 else 15) if alpha else 0
    payload_offset=offset+44
    header=b'\xabKTX 20\xbb\r\n\x1a\n'+struct.pack('<13I2Q',0,1,4,4,0,0,1,levels,compression,offset,44,0,0,0,0)
    index=b''.join(struct.pack('<3Q',payload_offset+16*i,16,16) for i in range(levels))
    path.write_bytes(header+index+dfd+bytes(16*levels))
    return path

class MaterialCodecProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()
    def check(self,role='colour-atlas',codec='basis-lz',mips=False,**kw):
        path=fixture(self.root/'s.ktx2',**kw)
        return module.inspect_ktx2(path,role,[4,4],mips,codec=codec)
    def test_legacy_colour_ETC1S_default(self):
        self.assertEqual(self.check(codec=None)['declared_codec'],'basis-lz')
    def test_legacy_colour_ETC1S_explicit(self):
        self.assertEqual(self.check()['color_model'],163)
    def test_legacy_linear_ETC1S_default(self):
        self.assertEqual(self.check('material-linear',None,transfer=1)['transfer'],1)
    def test_legacy_linear_ETC1S_explicit(self):
        self.assertEqual(self.check('material-linear','basis-lz',transfer=1)['supercompression'],1)
    def test_colour_UASTC_explicit_sRGB_Zstd(self):
        p=self.check(codec='uastc',model=166,compression=2)
        self.assertEqual((p['declared_codec'],p['color_model'],p['transfer'],p['supercompression']),('uastc',166,2,2))
    def test_linear_UASTC_explicit_linear_Zstd(self):
        p=self.check('material-linear','uastc',model=166,transfer=1,compression=2)
        self.assertEqual((p['declared_codec'],p['transfer']),('uastc',1))
    def test_UASTC_complete_mips_accepted(self):
        self.assertEqual(self.check(codec='uastc',mips=True,model=166,compression=2,levels=3)['levels'],3)
    def test_UASTC_not_implicit_default(self):
        with self.assertRaisesRegex(ValueError,'codec/transfer/primaries'):self.check(codec=None,model=166,compression=2)
    def test_ETC1S_declared_UASTC_rejected(self):
        with self.assertRaisesRegex(ValueError,'codec/transfer/primaries'):self.check(codec='uastc')
    def test_UASTC_declared_ETC1S_rejected(self):
        with self.assertRaisesRegex(ValueError,'codec/transfer/primaries'):self.check(model=166,compression=2)
    def test_colour_UASTC_linear_OETF_rejected(self):
        with self.assertRaisesRegex(ValueError,'codec/transfer/primaries'):self.check(codec='uastc',model=166,compression=2,transfer=1)
    def test_linear_UASTC_sRGB_OETF_rejected(self):
        with self.assertRaisesRegex(ValueError,'codec/transfer/primaries'):self.check('material-linear','uastc',model=166,compression=2)
    def test_UASTC_wrong_compression_rejected(self):
        with self.assertRaisesRegex(ValueError,'codec/transfer/primaries'):self.check(codec='uastc',model=166,compression=1)
    def test_unknown_material_codec_rejected(self):
        for role in ('colour-atlas','material-linear'):
            with self.subTest(role=role),self.assertRaisesRegex(ValueError,'codec must be basis-lz or uastc'):self.check(role,'astc')
    def test_missing_mips_rejected(self):
        with self.assertRaisesRegex(ValueError,'level count'):self.check(codec='uastc',mips=True,model=166,compression=2)
    def test_undeclared_mips_rejected(self):
        with self.assertRaisesRegex(ValueError,'level count'):self.check(codec='uastc',model=166,compression=2,levels=3)
    def test_premultiplied_material_rejected(self):
        with self.assertRaisesRegex(ValueError,'Premultiplied'):self.check(codec='uastc',model=166,compression=2,premult=True)
    def test_primaries_guard_unchanged(self):
        with self.assertRaisesRegex(ValueError,'codec/transfer/primaries'):self.check(codec='uastc',model=166,compression=2,primaries=0)
    def test_payload_bounds_guard_unchanged(self):
        p=fixture(self.root/'trunc.ktx2',model=166,compression=2)
        p.write_bytes(p.read_bytes()[:-1])
        with self.assertRaisesRegex(ValueError,'section bounds'):module.inspect_ktx2(p,'colour-atlas',[4,4],False,codec='uastc')
    def test_existing_RGBD_UASTC_alpha_guard(self):
        with self.assertRaisesRegex(ValueError,'requires encoded alpha'):self.check('rgbd','uastc',model=166,compression=2,transfer=1)
    def test_existing_tint_ETC1S_and_UASTC(self):
        self.assertTrue(self.check('tint','basis-lz',alpha=True)['alpha_present'])
        self.assertTrue(self.check('tint','uastc',model=166,compression=2,alpha=True)['alpha_present'])
    def test_actual_exported_UASTC_colour_RM_and_RGBD(self):
        report=json.loads(args.actual_report.read_text(encoding='utf-8-sig'))
        self.assertEqual(report['status'],'passed')
        profiles=[]
        for row in report['textures']:
            role='rgbd' if row.get('encoding')=='rgbd-v1' else ('colour-atlas' if row['color_space']=='srgb' else 'material-linear')
            path=args.actual_report.parent/row['output']
            self.assertEqual(sha(path),row['output_sha256'])
            self.assertEqual(row['codec'],'uastc')
            profiles.append({'path':str(path),'sha256':sha(path),'role':role,'profile':module.inspect_ktx2(path,role,row['dimensions'],row['mipmaps'],codec=row['codec'])})
        self.assertEqual(len(profiles),3)
        (args.output/'actual-profiles.json').write_text(json.dumps(profiles,indent=2))

suite=unittest.defaultTestLoader.loadTestsFromTestCase(MaterialCodecProfileTests)
with (args.output/'unittest.log').open('w',encoding='utf8') as log:
    result=unittest.TextTestRunner(stream=log,verbosity=2).run(suite)
after={str(p):sha(p) for p in args.source_blend}
version=ast.literal_eval(ast.parse((args.addon/'__init__.py').read_text(encoding='utf8')).body[0].value)['version']
record={'status':'passed' if result.wasSuccessful() and before==after and tuple(version)==(3, 3, 58) else 'failed','tests_run':result.testsRun,'failures':[{'test':str(t),'traceback':v} for t,v in result.failures],'errors':[{'test':str(t),'traceback':v} for t,v in result.errors],'helper':{'path':str(helper),'sha256':sha(helper)},'version':version,'source_hashes_before':before,'source_hashes_after':after,'source_unchanged':before==after,'actual_report':{'path':str(args.actual_report),'sha256':sha(args.actual_report)},'scope':'Synthetic metadata cases exercise parser guards, not actual bitstream validity; real exporter files separately inspected here and official ktx validate invoked by handoff. No scene save, image conversion, render or bake.'}
(args.output/'result.json').write_text(json.dumps(record,indent=2))
print(json.dumps(record,indent=2))
raise SystemExit(0 if record['status']=='passed' else 1)
