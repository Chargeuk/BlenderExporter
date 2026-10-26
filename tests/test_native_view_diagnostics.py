"""Opt-in native transport diagnostics preserve the original response contract."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src/babylon_js/environment_tools'))
spec=importlib.util.spec_from_file_location('native_view_diagnostics',ROOT/'src/babylon_js/workflow_tools/native_view_diagnostics.py')
diagnostics=importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnostics)
import test_native_recombine
from native_recombine import validate_original


class NativeDiagnosticTests(unittest.TestCase):
    def config(self,response=False):
        return {'_root':'project','_source':'project/source.blend','_config':'project/original.json',
            'receivers':{'names':['A']},'receiver_response':{'enabled':response},
            'capture':{'views':{'samples':128,'dimensions':[1200,900],'camera_set':[]},'room':{'enabled':False}}}

    def recipe(self):
        return {'schema_version':1,'mode':'native-transport','native_config':'original.json',
            'variants':['raw','processed'],'camera_ids':['near'],'samples':512}

    def validate(self,recipe,c,view=None):
        with patch('camera_views.validate_camera_set',return_value=[{'id':'near','camera':'Near','mode':'baked'}]):
            return diagnostics.validate_recipe(recipe,c,view)

    def test_legacy_default_and_single_variant_contract(self):
        c=self.config(True)
        recipe={'schema_version':1,'native_config':'original.json','camera_ids':['near']}
        self.validate(recipe,c)
        self.assertEqual(diagnostics.selected_variants(recipe),('raw','processed','physical-diffuse'))
        for variant in ('processed','processed-ao-bypass'):
            self.assertEqual(diagnostics.selected_variants(dict(recipe,variants=[variant])),(variant,))
        with self.assertRaises(ValueError):self.validate(recipe,self.config(False))
        with self.assertRaises(ValueError):diagnostics.selected_variants(dict(recipe,variants=['raw','processed']))

    def test_native_explicit_pair_requires_original_response_disabled(self):
        self.assertEqual(self.validate(self.recipe(),self.config())[0]['id'],'near')
        with self.assertRaises(ValueError):self.validate(self.recipe(),self.config(True))
        for mutation in ({'variants':['processed']},{'variants':['raw','processed','physical-diffuse']},
                         {'physical_samples':128},{'physical_objective_objects':['A']},
                         {'owned_evidence':{'objects':['A'],'chart_ids':[1]}},{'mode':'unknown'}):
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):
                self.validate(dict(self.recipe(),**mutation),self.config())
        c=self.config();c['finishing_overrides']={'anything':1}
        with self.assertRaises(ValueError):self.validate(self.recipe(),c)

    def test_capture_config_cannot_change_physical_source_or_other_capture(self):
        c=self.config();view=copy.deepcopy(c);view['_config']='project/near.json'
        view['capture']['views']['samples']=512
        self.assertIs(diagnostics.validate_view_config(c,view),view)
        for key,value in (('_source','other.blend'),('_root','other'),('receivers',{'names':['B']}),
                          ('receiver_response',{'enabled':True}),('view_only_binding',{'claim':'same'})):
            altered=copy.deepcopy(view);altered[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):diagnostics.validate_view_config(c,altered)
        altered=copy.deepcopy(view);altered['capture']['room']['enabled']=True
        with self.assertRaises(ValueError):diagnostics.validate_view_config(c,altered)

    def test_view_config_optin_not_allowed_in_legacy_mode(self):
        recipe={'schema_version':1,'native_config':'original.json','camera_ids':['near'],'view_config':'near.json'}
        with self.assertRaises(ValueError):self.validate(recipe,self.config(True))

    def test_light_exclusion_is_only_for_pure_baked_native_pair(self):
        result=diagnostics.light_policy(self.recipe(),['key','fill'],[])
        self.assertEqual(result['configured_bake_lights_excluded'],['key','fill'])
        with self.assertRaises(ValueError):diagnostics.light_policy({},['key'],[])
        with self.assertRaises(ValueError):diagnostics.light_policy(self.recipe(),[],['emitter'])
        with self.assertRaises(ValueError):diagnostics.light_policy(dict(self.recipe(),variants=['physical-diffuse']),['key'],[])

    def test_ordinary_padding_keeps_capped_owned_rgb_exact_without_mutating_inputs(self):
        labels=np.array([[1,0],[0,2]],np.int32)
        raw=np.zeros((2,2,4),np.float32);raw[0,0]=[.1,.2,.3,1];raw[1,1]=[4,5,6,1]
        original=raw.copy()
        result=diagnostics.ordinary_native_arrays(labels,raw,raw*1)
        for role,array in result.items():
            self.assertTrue(np.array_equal(array[labels>0],raw[labels>0]))
            self.assertTrue(np.array_equal(array[:,:,3],np.ones((2,2),np.float32)))
            self.assertTrue(all(tuple(v) in (tuple(raw[0,0]),tuple(raw[1,1])) for v in array.reshape(-1,4)))
        self.assertTrue(np.array_equal(raw,original))

    def test_padding_rejects_fabricated_coverage_nonfinite_and_unknown_labels(self):
        labels=np.array([[1,0],[0,2]],np.int32)
        raw=np.zeros((2,2,4),np.float32);raw[labels>0]=[1,2,3,1]
        for altered in (raw.astype(np.float64),raw[:,:,:3],raw+np.float32(np.nan),np.ones_like(raw)):
            with self.subTest(shape=altered.shape),self.assertRaises(ValueError):diagnostics.ordinary_native_arrays(labels,altered,raw)
        with self.assertRaises(ValueError):diagnostics.ordinary_native_arrays(labels.astype(float),raw,raw)

    def test_sampler_interpretations_are_explicit_and_not_silently_retagged(self):
        for role,tag in (('direct','Non-Color'),('indirect','Linear Rec.709'),('island_ids','Non-Color'),('ao','Non-Color'),('colour','sRGB')):
            diagnostics.native_sampler_colorspace(role,tag)
        for role,tag in (('direct','sRGB'),('indirect','sRGB'),('island_ids','Linear Rec.709'),('colour','Non-Color'),('mystery','Non-Color')):
            with self.subTest(role=role,tag=tag),self.assertRaises(ValueError):diagnostics.native_sampler_colorspace(role,tag)

    def fixture(self,root):
        c,candidate,tools,build=test_native_recombine.NativeRecombineTests().fixture(root)
        for role in ('direct','indirect'):
            raw=np.load(candidate/'bake'/(role+'_raw.npy'))
            # Ownership output may have metallic-normalized RGB different from joined native RGB.
            raw[:,:,:3]*=np.float32(2)
            np.save(candidate/'ownership'/(role+'_raw.npy'),raw)
        for name in ('combined.exr','combined.provenance.json'):(candidate/'masters'/name).write_bytes(b'completed')
        outputs={str(path.relative_to(candidate)):diagnostics.sha(path) for path in candidate.rglob('*') if path.is_file() and path.name!='build.json'}
        build['stages']['combine']['outputs']=outputs
        (candidate/'build.json').write_text(json.dumps(build))
        return c,candidate,tools

    def test_full_original_native_proof_precedes_raw_derivation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);c,candidate,tools=self.fixture(root)
            plan=validate_original(c,candidate,tools)
            output=root/'diagnostic';output.mkdir();inputs=dict(plan['inputs'])
            evidence=diagnostics.prepare_native_raw(candidate,output,inputs)
            self.assertEqual(evidence['owned_pixels'],2)
            self.assertTrue(all(row['owned_exact'] for row in evidence['maps'].values()))
            self.assertTrue(all(Path(row['array']).is_relative_to(output) for row in evidence['maps'].values()))
            self.assertEqual(float(np.load(evidence['maps']['indirect']['array'])[1,1,0]),4)
            diagnostics.verify_inputs(inputs)
            raw=candidate/'ownership/direct_raw.npy';raw.write_bytes(b'changed original output')
            with self.assertRaises(ValueError):diagnostics.prepare_native_raw(candidate,root/'another',inputs)

    def test_unproven_ownership_output_or_changed_original_tool_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);c,candidate,tools=self.fixture(root)
            plan=validate_original(c,candidate,tools);inputs=dict(plan['inputs'])
            inputs.pop(str((candidate/'ownership/direct_raw.npy').resolve()))
            with self.assertRaises(ValueError):diagnostics.prepare_native_raw(candidate,root/'diagnostic',inputs)
            (tools/'src/babylon_js/environment_tools/worker.py').write_text('# changed')
            with self.assertRaises(ValueError):validate_original(c,candidate,tools)

    def test_prepare_verifies_original_config_not_separate_capture_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);c,candidate,tools=self.fixture(root)
            plan=validate_original(c,candidate,tools)
            c.update(self.config())
            c['_root']=str(root);c['_source']=str(root/'source.blend');c['_config']=str(root/'recipe.json')
            view=copy.deepcopy(c);view['_config']=str(root/'near.json');view['capture']['views']['samples']=512
            Path(view['_config']).write_text('{}')
            recipe=dict(self.recipe(),native_config=c['_config'],view_config=view['_config'])
            recipe_path=root/'diagnostic.json';recipe_path.write_text(json.dumps(recipe))
            with patch('common.load_config',side_effect=[c,view]),patch('native_recombine.validate_original',return_value=plan) as validate,\
                 patch('camera_views.validate_camera_set',return_value=[{'id':'near','mode':'baked'}]):
                _,proof=diagnostics.prepare(recipe_path,candidate,tools,root/'output')
            validate.assert_called_once_with(c,candidate,tools)
            self.assertEqual(proof['native_config'],c['_config'])
            self.assertEqual(proof['view_config'],view['_config'])
            self.assertIn(view['_config'],proof['inputs'])
            self.assertIn('native_raw',proof)


    def test_appearance_sampler_is_srgb_and_unknown_roles_remain_rejected(self):
        diagnostics.native_sampler_colorspace('colour_adjustment','sRGB')
        for role,space in [('colour_adjustment','Non-Color'),('unknown','sRGB'),('direct','sRGB'),('island_ids','sRGB')]:
            with self.subTest(role=role,space=space),self.assertRaises(ValueError):diagnostics.native_sampler_colorspace(role,space)


if __name__=='__main__':unittest.main()
