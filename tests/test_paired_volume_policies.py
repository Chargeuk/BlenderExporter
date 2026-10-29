"""CPU regressions for canonical producer policy and actual retained proof inputs."""
import argparse,copy,hashlib,importlib.util,json,sys,tempfile,unittest
from pathlib import Path
import numpy as np
p=argparse.ArgumentParser();p.add_argument('--addon',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--baseline-report',type=Path,required=True);p.add_argument('--policy-report',type=Path,required=True);p.add_argument('--source-copy',action='append',default=[],help='Original master path=retained byte-identical copy; producer report is never edited')
a=p.parse_args();assert not a.output.exists();a.output.mkdir(parents=True)
spec=importlib.util.spec_from_file_location('paired_policy_under_test',a.addon/'workflow_tools/decoded_delivery.py');dd=importlib.util.module_from_spec(spec);spec.loader.exec_module(dd)
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
baseline=json.loads(a.baseline_report.read_text())['stage_b']['report'];policy=json.loads(a.policy_report.read_text())['stage_b']['report']
input_before={str(p):sha(p) for p in (a.baseline_report,a.policy_report)}
source_copies=[]
for binding in a.source_copy:
    original,copied=binding.split('=',1);copied=Path(copied).resolve()
    matched=0
    for report in (baseline,policy):
        for row in (report,report['advanced']):
            if Path(row['source']).resolve()==Path(original).resolve():
                expected=row['metrics']['source_sha256']
                if not copied.is_file() or sha(copied)!=expected:
                    raise ValueError('Retained source-copy bytes differ from original producer guard')
                source_copies.append({'original':original,'copy':str(copied),'sha256':expected})
                row['source']=str(copied);matched+=1
    if not matched:raise ValueError('Source-copy does not match any producer master')


class CanonicalSimpleModeTests(unittest.TestCase):
    def setUp(self):
        y,x=np.mgrid[:8,:8];self.master=np.stack((.2+(x*x+y)/9,.3+(x+y*y)/12,.5+(x*x+y*y)/15),axis=-1).astype(np.float32)
    def row(self,mode):
        pixels,_=dd.core.encode_rgbd(self.master,mode)
        m={'source_sha256':'a'*64,'prepared_sha256':'b'*64,'rgbd_encoded_divisor_mode':mode,'rgbd_divisor_mode':mode}
        m['source_preparation_guard']={'when':'before-ktx-create','byte_exact':True,'divisor_mode':mode,'source_sha256':m['source_sha256'],'prepared_sha256':m['prepared_sha256'],'rgba_sha256':hashlib.sha256(pixels.tobytes()).hexdigest()}
        return {'metrics':m},pixels
    def test_legacy_default_perpixel_without_new_guard(self):
        row,pixels=self.row('per-pixel');row['metrics'].pop('source_preparation_guard');row['metrics'].pop('rgbd_divisor_mode')
        self.assertEqual(dd._validate_volume_preparation(row,self.master,pixels,False)['rgbd_encoded_divisor_mode'],'per-pixel')
    def test_explicit_perpixel_guard(self):
        row,pixels=self.row('per-pixel');self.assertEqual(dd._volume_simple_mode(row),'per-pixel');dd._validate_volume_preparation(row,self.master,pixels,False)
    def test_block_mode_requires_whole_master_canonical(self):
        row,pixels=self.row('block-4x4');self.assertEqual(dd._validate_volume_preparation(row,self.master,pixels,False)['rgbd_encoded_divisor_mode'],'block-4x4')
    def test_unknown_mode_rejected(self):
        row,pixels=self.row('per-pixel');row['metrics']['rgbd_encoded_divisor_mode']='image-wide'
        with self.assertRaisesRegex(ValueError,'Unsupported or mismatched'):dd._validate_volume_preparation(row,self.master,pixels,False)
    def test_mode_and_guard_mismatch_rejected(self):
        row,pixels=self.row('block-4x4');row['metrics']['source_preparation_guard']['divisor_mode']='per-pixel'
        with self.assertRaisesRegex(ValueError,'preparation guard differs'):dd._validate_volume_preparation(row,self.master,pixels,False)
    def test_block_missing_guard_rejected(self):
        row,pixels=self.row('block-4x4');row['metrics'].pop('source_preparation_guard')
        with self.assertRaisesRegex(ValueError,'requires source preparation guard'):dd._validate_volume_preparation(row,self.master,pixels,False)
    def test_wrong_canonical_preparation_rejected(self):
        row,_=self.row('block-4x4');other,_=dd.core.encode_rgbd(self.master,'per-pixel')
        with self.assertRaisesRegex(ValueError,'canonical unflipped'):dd._validate_volume_preparation(row,self.master,other,False)
    def test_raw_RGBA_guard_mismatch_rejected(self):
        row,pixels=self.row('block-4x4');row['metrics']['source_preparation_guard']['rgba_sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'raw RGBA differs'):dd._validate_volume_preparation(row,self.master,pixels,False)
    def test_nonhash_source_guard_rejected(self):
        row,pixels=self.row('block-4x4');row['metrics']['source_sha256']=None;row['metrics']['source_preparation_guard']['source_sha256']=None
        with self.assertRaisesRegex(ValueError,'preparation guard differs'):dd._validate_volume_preparation(row,self.master,pixels,False)
    def test_actual_block_prepared_from_unchanged_master(self):
        row=policy;master=dd.volume_linear(row['source']);prepared=dd.volume_png_rgba(row['metrics']['prepared'])
        self.assertEqual(dd._validate_volume_preparation(row,master,prepared,False)['rgbd_encoded_divisor_mode'],'block-4x4')

class ActualEncoderProofTests(unittest.TestCase):
    def setUp(self):
        self.row=copy.deepcopy(policy['advanced']);self.prepared=Path(self.row['metrics']['prepared']);self.final=a.policy_report.parent/'package'/self.row['texture']
        self.tmp=tempfile.TemporaryDirectory();self.temp=Path(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()
    def check(self,advanced=True):return dd._volume_encoder_policy(self.row,self.prepared,self.final,advanced=advanced)
    def test_legacy_direct_command_default_accepted(self):
        row=baseline['advanced'];prepared=Path(row['metrics']['prepared']);compressed=a.baseline_report.parent/'package'/row['texture']
        self.assertIsNone(dd._volume_encoder_policy(row,prepared,compressed,advanced=True)['source_ceiling_preflight'])
    def test_actual_source_ceiling_retained_unconstrained_command(self):
        result=self.check();self.assertTrue(result['source_ceiling_preflight']['alpha_enforcement_claim'])
        self.assertIn(str(Path(self.row['advancedAlphaPolicy']['original']).resolve()),result['guarded_inputs'])
    def test_source_ceiling_on_Simple_rejected(self):
        with self.assertRaisesRegex(ValueError,'unsupported volume alpha policy'):self.check(False)
    def test_unknown_policy_rejected(self):
        self.row['advancedAlphaPolicy']['policy']='other'
        with self.assertRaisesRegex(ValueError,'unsupported volume alpha policy'):self.check()
    def test_missing_policy_for_unconstrained_command_rejected(self):
        self.row.pop('advancedAlphaPolicy')
        with self.assertRaisesRegex(ValueError,'must name actual final'):self.check()
    def test_null_present_proof_rejected(self):
        self.row['advancedAlphaPolicy']=None
        with self.assertRaisesRegex(ValueError,'unsupported volume alpha policy'):self.check()
    def test_corrupt_sourceKTX_hash_rejected(self):
        self.row['advancedAlphaPolicy']['original_sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'original/final/prepared inputs differ'):self.check()
    def test_corrupt_finalKTX_hash_rejected(self):
        self.row['advancedAlphaPolicy']['target_sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'original/final/prepared inputs differ'):self.check()
    def test_unbound_command_target_rejected(self):
        self.row['command'][-1]=str(self.temp/'missing_unconstrained.ktx2')
        with self.assertRaisesRegex(ValueError,'original/final/prepared inputs differ'):self.check()
    def test_mismatched_original_D_rejected(self):
        self.row['advancedAlphaPolicy']['original_divisor_byte']+=1
        with self.assertRaisesRegex(ValueError,'prepared divisor differs'):self.check()
    def test_mismatched_minDivisor_rejected(self):
        self.row['advancedAlphaPolicy']['minimum_divisor']=15/255
        with self.assertRaisesRegex(ValueError,'prepared divisor differs'):self.check()
    def test_source_guard_corrupt_rejected(self):
        self.row['metrics']['source_preparation_guard']['source_sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'source preparation guard differs'):self.check()
    def test_raw_before_hash_corrupt_rejected(self):
        self.row['advancedAlphaPolicy']['before_raw_sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'native RGBA changed'):self.check()
    def test_missing_successful_decode_call_rejected(self):
        self.row['advancedAlphaPolicy']['calls']=[]
        with self.assertRaisesRegex(ValueError,'decode is not bound'):self.check()
    def test_changed_RGB_proof_rejected(self):
        self.row['advancedAlphaPolicy']['RGB_after_sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'invariance proof differs'):self.check()
    def test_actual_changed_RGB_with_rehashed_raw_still_rejected(self):
        proof=self.row['advancedAlphaPolicy'];original=Path(proof['after_raw']);data=np.fromfile(original,dtype=np.uint8).copy();data[0]^=1
        path=self.temp/'changed.rgba';data.tofile(path);proof['after_raw']=str(path);proof['after_raw_sha256']=sha(path)
        for call in proof['calls']:
            if call.get('argv',[])[-1:]==[str(original)]:call['argv'][-1]=str(path)
        with self.assertRaisesRegex(ValueError,'invariance proof differs|RGB bytes changed'):self.check()
    def test_official_final_decode_must_equal_producer_after_raw(self):
        from volume_audit import validate_advanced_alpha
        pixels=dd.volume_png_rgba(self.prepared);data=np.fromfile(self.row['advancedAlphaPolicy']['after_raw'],dtype=np.uint8).reshape(pixels.shape).copy();data[0,0,0]^=1
        path=self.temp/'official-changed.rgba';data.tofile(path)
        with self.assertRaisesRegex(ValueError,'invariance proof differs'):validate_advanced_alpha(self.row,pixels,data,path,self.final)

suite=unittest.TestSuite([unittest.defaultTestLoader.loadTestsFromTestCase(c) for c in (CanonicalSimpleModeTests,ActualEncoderProofTests)])
with (a.output/'unittest.log').open('w') as log:r=unittest.TextTestRunner(stream=log,verbosity=2).run(suite)
unchanged=all(sha(path)==d for path,d in input_before.items())
record={'status':'passed' if r.wasSuccessful() and unchanged else 'failed','tests_run':r.testsRun,'failures':[{'test':str(t),'traceback':x} for t,x in r.failures],'errors':[{'test':str(t),'traceback':x} for t,x in r.errors],'helper':str(a.addon/'workflow_tools/decoded_delivery.py'),'helper_sha256':sha(a.addon/'workflow_tools/decoded_delivery.py'),'actual_producer_reports':input_before,'test_consumer_source_copy_bindings':source_copies,'producer_reports_unchanged':unchanged,'scope':'Pure canonical/proof policy tests and real retainedP1files, no encoder, GPU, scene save or qualityexception. Full actualP1pairedofficial extraction/integration follows after packagedinstallation.'}
(a.output/'result.json').write_text(json.dumps(record,indent=2));print(json.dumps(record,indent=2));raise SystemExit(0 if record['status']=='passed' else 1)
