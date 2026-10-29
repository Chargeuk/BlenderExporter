"""CPU unit regressions for option resolution and post-encode proof guards."""
import argparse,ast,copy,hashlib,importlib.util,json,sys,tempfile,unittest
from pathlib import Path
import numpy as np
p=argparse.ArgumentParser();p.add_argument('--addon',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
a=p.parse_args();assert not a.output.exists();a.output.mkdir(parents=True)
sys.path.insert(0,str(a.addon/'environment_tools'));sys.path.insert(0,str(a.addon/'workflow_tools'))
import volume_assembly as assembly
import volume_audit as audit
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()

class ExplicitOptionsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.source=self.root/'source.blend';self.source.write_bytes(b'unit-option-source-not-opened-by-Blender')
        self.master=self.root/'master.exr';self.master.write_bytes(b'unit-path-binding-only-not-decoded')
        self.receipt=self.root/'receipt.json';self.receipt.write_text(json.dumps({'master':'master.exr','masterSha256':sha(self.master),'source':{'scene':str(self.source),'sceneSha256':sha(self.source)}}))
        self.config={'package_root':str(self.root),'source':self.source.name,'source_sha256':sha(self.source),'ktx_options':{'environment_lighting_volume':{'config':'receipt.json','image':'master.exr','strength':1,'simple_divisor_mode':'per-pixel'}},'bound_dependencies':[{'path':x.name,'sha256':sha(x)} for x in (self.receipt,self.master)]}
        self.path=self.root/'export.json'
    def tearDown(self):self.tmp.cleanup()
    def resolve(self,saved=None):return assembly.resolve_stage_b_options(self.config,self.path,saved)
    def test_legacy_saved_only_unchanged(self):
        self.config.pop('ktx_options');saved={'config':'//receipt.json','image':'//master.exr','strength':1}
        self.assertIs(self.resolve(saved),saved)
    def test_explicit_clean_source_paths_bound(self):
        before=sha(self.source);result=self.resolve()
        self.assertEqual(result['config'],str(self.receipt.resolve()));self.assertEqual(result['image'],str(self.master.resolve()))
        self.assertEqual(result['strength'],1);self.assertEqual(sha(self.source),before)
    def test_saved_and_explicit_identical_allowed(self):
        self.assertEqual(self.resolve({'config':'//receipt.json','image':'//master.exr','strength':1})['strength'],1)
    def test_missing_both_rejected(self):
        self.config.pop('ktx_options')
        with self.assertRaisesRegex(ValueError,'Missing saved or explicit'):self.resolve()
    def test_explicit_null_rejected(self):
        self.config['ktx_options']['environment_lighting_volume']=None
        with self.assertRaisesRegex(ValueError,'Explicit volume requires'):self.resolve()
    def test_unbound_master_rejected(self):
        self.config['bound_dependencies']=self.config['bound_dependencies'][:1]
        with self.assertRaisesRegex(ValueError,'Unbound or changed'):self.resolve()
    def test_changed_bound_receipt_rejected(self):
        self.receipt.write_text('{}')
        with self.assertRaisesRegex(ValueError,'Unbound or changed'):self.resolve()
    def test_bound_but_wrong_receipt_source_rejected(self):
        record=json.loads(self.receipt.read_text());record['source']['sceneSha256']='0'*64;self.receipt.write_text(json.dumps(record))
        self.config['bound_dependencies'][0]['sha256']=sha(self.receipt)
        with self.assertRaisesRegex(ValueError,'receipt source/master differs'):self.resolve()
    def test_bound_but_wrong_receipt_master_rejected(self):
        record=json.loads(self.receipt.read_text());record['masterSha256']='0'*64;self.receipt.write_text(json.dumps(record))
        self.config['bound_dependencies'][0]['sha256']=sha(self.receipt)
        with self.assertRaisesRegex(ValueError,'receipt source/master differs'):self.resolve()
    def test_strength_bool_zero_fraction_rejected(self):
        for v in (True,False,0,.5):
            with self.subTest(v=v):
                self.config['ktx_options']['environment_lighting_volume']['strength']=v
                with self.assertRaisesRegex(ValueError,'strength must be one'):self.resolve()
    def test_unknown_policy_rejected(self):
        self.config['ktx_options']['environment_lighting_volume']['advanced_alpha_policy']='relaxed'
        with self.assertRaisesRegex(ValueError,'Unsupported explicit Advanced'):self.resolve()
    def test_unknown_explicit_key_rejected(self):
        self.config['ktx_options']['environment_lighting_volume']['skip_source_validation']=True
        with self.assertRaisesRegex(ValueError,'supported policies'):self.resolve()
    def test_saved_strength_conflict_rejected(self):
        with self.assertRaisesRegex(ValueError,'associations conflict'):self.resolve({'config':'//receipt.json','image':'//master.exr','strength':0})
    def test_saved_policy_conflict_rejected(self):
        with self.assertRaisesRegex(ValueError,'associations conflict'):self.resolve({'config':'//receipt.json','image':'//master.exr','strength':1,'simple_divisor_mode':'block-4x4'})
    def test_saved_master_conflict_rejected(self):
        with self.assertRaisesRegex(ValueError,'associations conflict'):self.resolve({'config':'//receipt.json','image':'//other.exr','strength':1})
    def test_supported_existing_policies_accepted(self):
        self.config['ktx_options']['environment_lighting_volume'].update(simple_divisor_mode='block-4x4',advanced_alpha_policy='source-ceiling')
        self.assertEqual(self.resolve()['advanced_alpha_policy'],'source-ceiling')

class AdvancedDefaultAndProofTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.ktx=self.root/'advanced.ktx2';self.ktx.write_bytes(b'unit-proof-linkage-not-codec-profile')
        self.png=self.root/'prepared.png';self.png.write_bytes(b'unit-preparation-linkage')
        self.prepared=np.array([[[80,90,100,20],[70,80,90,20]]],dtype=np.uint8)
        self.decoded=self.prepared.copy();self.raw=self.root/'decoded.rgba';self.decoded.tofile(self.raw)
        self.conversion={'texture':self.ktx.name,'textureSha256':sha(self.ktx),'metrics':{'prepared':str(self.png)},'command':['ktx','create','--format','R8G8B8A8_UNORM','--assign-oetf','linear','--encode','uastc','--threads','8','--uastc-quality','4','--zstd','3',str(self.png),str(self.ktx)]}
    def tearDown(self):self.tmp.cleanup()
    def check(self,**kw):return audit.validate_advanced_alpha(self.conversion,self.prepared,self.decoded,self.raw,self.ktx,**kw)
    def proof(self):
        before=self.root/'before.rgba';self.decoded.tofile(before)
        rgb=hashlib.sha256(self.decoded[...,:3].tobytes()).hexdigest()
        self.conversion['advancedAlphaPolicy']={'policy':'source-ceiling','RGB_byte_exact':True,'RGB_before_sha256':rgb,'RGB_after_sha256':rgb,'before_raw':str(before),'before_raw_sha256':sha(before),'after_raw':str(self.raw),'after_raw_sha256':sha(self.raw)}
    def test_default_direct_encode_no_enforcement_claim(self):
        r=self.check();self.assertEqual(r['mode'],'direct-default-encode');self.assertFalse(r['alpha_enforcement_claim'])
    def test_default_lossless_package_copy_lineage(self):
        current=self.root/'copy';current.mkdir();dest=current/self.ktx.name;dest.write_bytes(self.ktx.read_bytes())
        r=audit.validate_advanced_alpha(self.conversion,self.prepared,self.decoded,self.raw,dest,producer_package=self.root,package_files={self.ktx.name:sha(self.ktx)})
        self.assertTrue(r['verified_copy_lineage']['assembly_closure_verified'])
    def test_default_copy_without_closure_rejected(self):
        current=self.root/'copy';current.mkdir();dest=current/self.ktx.name;dest.write_bytes(self.ktx.read_bytes())
        with self.assertRaisesRegex(ValueError,'copy lacks'):audit.validate_advanced_alpha(self.conversion,self.prepared,self.decoded,self.raw,dest)
    def test_default_missing_command_target_rejected(self):
        self.conversion['command'][-1]=str(self.root/'missing.ktx2')
        with self.assertRaisesRegex(ValueError,'actual final encoded'):self.check()
    def test_default_unconstrained_postprocess_target_rejected(self):
        p=self.root/'advanced_unconstrained.ktx2';p.write_bytes(self.ktx.read_bytes());self.conversion['command'][-1]=str(p)
        with self.assertRaisesRegex(ValueError,'actual final encoded'):self.check()
    def test_default_unconstrained_not_allowed_as_renamed_final(self):
        p=self.root/'advanced_unconstrained.ktx2';p.write_bytes(self.ktx.read_bytes())
        self.conversion['texture']=p.name;self.conversion['command'][-1]=str(p)
        with self.assertRaisesRegex(ValueError,'actual final encoded'):audit.validate_advanced_alpha(self.conversion,self.prepared,self.decoded,self.raw,p)
    def test_default_wrong_output_hash_rejected(self):
        self.conversion['textureSha256']='0'*64
        with self.assertRaisesRegex(ValueError,'actual final encoded'):self.check()
    def test_default_wrong_codec_command_rejected(self):
        self.conversion['command'][self.conversion['command'].index('--encode')+1]='basis-lz'
        with self.assertRaisesRegex(ValueError,'encode option differs'):self.check()
    def test_default_mips_rejected(self):
        self.conversion['command'].insert(2,'--generate-mipmap')
        with self.assertRaisesRegex(ValueError,'must not generate mipmaps'):self.check()
    def test_default_prepared_input_mismatch_rejected(self):
        self.conversion['command'][-2]=str(self.root/'other.png')
        with self.assertRaisesRegex(ValueError,'prepared input differs'):self.check()
    def test_default_alpha_overshoot_still_rejected(self):
        self.decoded[0,0,3]=21
        with self.assertRaisesRegex(ValueError,'divisor ceiling failed'):self.check()
    def test_default_nonconstant_preparation_rejected(self):
        self.prepared[0,0,3]=19
        with self.assertRaisesRegex(ValueError,'divisor ceiling failed'):self.check()
    def test_source_ceiling_strict_proof_accepted(self):
        self.proof();self.assertTrue(self.check()['alpha_enforcement_claim'])
    def test_present_null_proof_rejected(self):
        self.conversion['advancedAlphaPolicy']=None
        with self.assertRaisesRegex(ValueError,'Invalid or unknown'):self.check()
    def test_unknown_present_policy_rejected(self):
        self.proof();self.conversion['advancedAlphaPolicy']['policy']='other'
        with self.assertRaisesRegex(ValueError,'Invalid or unknown'):self.check()
    def test_corrupt_present_RGB_proof_rejected(self):
        self.proof();self.conversion['advancedAlphaPolicy']['RGB_after_sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'invariance proof differs'):self.check()
    def test_changed_proof_raw_bytes_rejected(self):
        self.proof();Path(self.conversion['advancedAlphaPolicy']['before_raw']).write_bytes(b'wrong')
        with self.assertRaisesRegex(ValueError,'invariance proof differs'):self.check()

suite=unittest.TestSuite([unittest.defaultTestLoader.loadTestsFromTestCase(c) for c in (ExplicitOptionsTests,AdvancedDefaultAndProofTests)])
with (a.output/'unittest.log').open('w') as log:r=unittest.TextTestRunner(stream=log,verbosity=2).run(suite)
record={'status':'passed' if r.wasSuccessful() else 'failed','tests_run':r.testsRun,'failures':[{'test':str(t),'traceback':x} for t,x in r.failures],'errors':[{'test':str(t),'traceback':x} for t,x in r.errors],'modules':{str(Path(m.__file__).resolve()):sha(m.__file__) for m in (assembly,audit)},'version':ast.literal_eval(ast.parse((a.addon/'__init__.py').read_text()).body[0].value)['version'],'scope':'Unit option/proof guards only; no fake data promoted as actual codec/source proof. Real S/receipt preflight and full normal model integration run separately.'}
(a.output/'result.json').write_text(json.dumps(record,indent=2));print(json.dumps(record,indent=2));raise SystemExit(0 if r.wasSuccessful() else 1)
