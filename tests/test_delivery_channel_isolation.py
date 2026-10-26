"""Controlled delivery selection, immutable binding receipts and legacy behavior."""
import copy
from contextlib import contextmanager
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src/babylon_js/environment_tools'))
from common import sha
from delivery_views import delivery_channel_plan,selected_channel_bindings,contract_for_views,delivery_materials
from camera_views import validate_camera_set,_capture_modes,capture_views


class DeliveryChannelIsolationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.spec={'delivery_contract':'contract.json','dimensions':[64,48],'samples':4,
                   'display':{'view_transform':'Raw','look':'None','exposure':0,'gamma':1},
                   'camera_set':[{'id':'ref','camera':'Camera','mode':'delivery-reference'},
                                 {'id':'dec','camera':'Camera','mode':'delivery-decoded'}]}
        self.policy={'schema_version':1,'variants':{
            'albedo-decoded':{'colour_atlases':'decoded','lightmap':'reference'},
            'lightmap-decoded':{'colour_atlases':'reference','lightmap':'decoded'}}}
        self.record={'bindings':{'colour_atlases':[]},'generated':{},'contract_sha256':'a'*64,
                     'model_contract':{'minimum_divisor':.5,'tint_strength':0},'limits':'fixture limits'}
        self.record['bindings']['lightmap']=self.binding('lightmap')

    def binding(self,name):
        binding={'prepared':str(self.root/(name+'.png')),'prepared_sha256':'b'*64,
                 'ktx':str(self.root/(name+'.ktx2')),'sha256':'c'*64}
        for variant,value in [('reference',31),('decoded',73)]:
            path=self.root/(name+'_'+variant+'.npy')
            np.save(path,np.full((2,2,4),value,dtype=np.uint8),allow_pickle=False)
            binding[variant+'_array']=str(path);self.record['generated'][str(path)]=sha(path)
        return binding

    def isolated(self):
        spec=copy.deepcopy(self.spec);spec['delivery_channel_isolation']=copy.deepcopy(self.policy)
        spec['camera_set'] += [{'id':name,'camera':'Camera','mode':'delivery-isolation-'+name}
                               for name in self.policy['variants']]
        return spec

    def test_default_plan_and_camera_pair_remain_unchanged(self):
        before=copy.deepcopy(self.spec)
        self.assertEqual(delivery_channel_plan(self.spec),{
            'delivery-reference':dict(colour_atlases='reference',lightmap='reference',tint='reference'),
            'delivery-decoded':dict(colour_atlases='decoded',lightmap='decoded',tint='decoded')})
        self.assertEqual(validate_camera_set({'scene':'Scene','capture':{'views':self.spec}}),self.spec['camera_set'])
        self.assertEqual(self.spec,before)

    def test_four_variants_require_matching_cameras_and_all_declared_modes(self):
        spec=self.isolated();config={'scene':'Scene','capture':{'views':spec}}
        self.assertEqual(len(validate_camera_set(config)),4)
        spec['camera_set'][-1]['lens']=45
        with self.assertRaisesRegex(ValueError,'identical camera settings'):validate_camera_set(config)
        del spec['camera_set'][-1]['lens'];spec['camera_set'].pop()
        with self.assertRaisesRegex(ValueError,'each declared isolation'):validate_camera_set(config)
        spec=self.isolated();spec['camera_set'][-1]['mode']='delivery-isolation-unlisted'
        with self.assertRaises(ValueError):validate_camera_set({'scene':'Scene','capture':{'views':spec}})

    def test_isolation_rejects_existing_stage_before_blender_and_case_collisions(self):
        spec=self.isolated();config={'scene':'Scene','capture':{'views':spec}}
        (self.root/'delivery-images').mkdir()
        with self.assertRaisesRegex(ValueError,'fresh output directory'):capture_views(config,self.root,self.root)
        spec['camera_set'][-1]['id']='REF'
        with self.assertRaisesRegex(ValueError,'colliding'):validate_camera_set(config)

    def test_existing_capture_dispatch_enters_each_declared_material_context_once(self):
        spec=self.isolated();events=[]
        @contextmanager
        def context(mode):
            events.append(mode);yield {'mode':mode}
        def forbidden():raise AssertionError('Unexpected physical/baked context')
        contexts=[(mode,lambda mode=mode:context(mode)) for mode in delivery_channel_plan(spec,self.record)]
        rendered=_capture_modes(spec['camera_set'],spec,forbidden,forbidden,
                               lambda entry,mode,receipt:(entry['id'],mode,receipt['mode']),contexts)
        self.assertEqual(events,list(delivery_channel_plan(spec,self.record)))
        self.assertEqual([row[0] for row in rendered],[entry['id'] for entry in spec['camera_set']])
        self.assertTrue(all(mode==actual for _,mode,actual in rendered))

    def test_policy_rejects_unknown_fields_types_values_and_missing_contract(self):
        bad=[None,{},dict(self.policy,unexpected=True),dict(self.policy,schema_version=True),
             dict(self.policy,variants={}),dict(self.policy,variants={'Bad Name':{'lightmap':'reference','colour_atlases':'decoded'}}),
             dict(self.policy,variants={'mix':{'lightmap':'reference','colour_atlases':'decoded','exposure':0}}),
             dict(self.policy,variants={'mix':{'lightmap':'REFERENCE','colour_atlases':'decoded'}}),
             dict(self.policy,variants={'mix':{'lightmap':'decoded'}})]
        for policy in bad:
            with self.subTest(policy=policy):
                spec=copy.deepcopy(self.spec);spec['delivery_channel_isolation']=policy
                with self.assertRaises(ValueError):delivery_channel_plan(spec)
        spec=self.isolated();del spec['delivery_contract']
        with self.assertRaises(ValueError):delivery_channel_plan(spec)

    def test_tint_can_only_select_existing_binding_and_is_explicit(self):
        spec=self.isolated();spec['delivery_channel_isolation']['variants']['albedo-decoded']['tint']='decoded'
        with self.assertRaisesRegex(ValueError,'absent tint'):delivery_channel_plan(spec,self.record)
        self.record['bindings']['tint']=self.binding('tint')
        with self.assertRaisesRegex(ValueError,'explicitly select'):delivery_channel_plan(spec,self.record)
        spec['delivery_channel_isolation']['variants']['lightmap-decoded']['tint']='reference'
        plan=delivery_channel_plan(spec,self.record)
        self.assertEqual(plan['delivery-isolation-albedo-decoded']['tint'],'decoded')

    def test_selected_receipt_uses_actual_immutable_arrays_per_channel(self):
        atlas=self.binding('atlas');atlas['image_name']='SourceAtlas'
        self.record['bindings']['colour_atlases']=[atlas]
        self.record['bindings']['tint']=self.binding('tint')
        before=copy.deepcopy(self.record)
        selection=dict(lightmap='reference',colour_atlases='decoded',tint='reference')
        rows=selected_channel_bindings(self.record,selection)
        self.assertEqual([(row['channel'],row['variant']) for row in rows],
                         [('lightmap','reference'),('colour_atlases','decoded'),('tint','reference')])
        for row in rows:
            self.assertEqual(row['array_sha256'],sha(row['array']))
            self.assertIn(row['array'],self.record['generated'])
            self.assertEqual(row['ktx_sha256'],'c'*64)
        self.assertEqual(rows[1]['image_name'],'SourceAtlas')
        self.assertEqual(self.record,before)
        path=Path(rows[1]['array']);path.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'unchanged hash-validated'):selected_channel_bindings(self.record,selection)

    def test_selected_array_cannot_escape_validated_generated_set(self):
        del self.record['generated'][self.record['bindings']['lightmap']['decoded_array']]
        with self.assertRaisesRegex(ValueError,'hash-validated'):
            selected_channel_bindings(self.record,dict(lightmap='decoded',colour_atlases='reference'))

    def test_contract_entry_uses_existing_loader_and_does_not_retag_record(self):
        source=self.root/'source.blend';source.write_bytes(b'source')
        contract=self.root/'contract.json';contract.write_text('{}')
        record=copy.deepcopy(self.record);record['source_sha256']=sha(source)
        original=copy.deepcopy(record);loader=ModuleType('decoded_delivery')
        def load(path):
            self.assertEqual(path,contract);return copy.deepcopy(record)
        loader.load_contract=load
        config={'_root':str(self.root),'_source':str(source),'capture':{'views':self.isolated()}}
        with patch.dict(sys.modules,{'decoded_delivery':loader}):actual=contract_for_views(config)
        self.assertEqual(record,original)
        self.assertEqual(actual['generated'],original['generated'])
        self.assertEqual(actual['contract_sha256'],sha(contract))

    @contextmanager
    def material_runtime(self,max_path=None):
        package=ModuleType('babylon_js');package.colour_adjustment=SimpleNamespace()
        ktx=ModuleType('babylon_js.ktx_export');appearance=ModuleType('appearance')
        written=[];removed=[]
        def write(path,array):
            if max_path is not None and len(str(path))>=max_path:
                raise FileNotFoundError('Simulated Windows MAX_PATH image write')
            written.append((path,array.copy()));path.write_bytes(array.tobytes())
        ktx._write_png=write;appearance.preview_identity=lambda material:'unused'
        def load(path,check_existing):
            self.assertFalse(check_existing)
            return SimpleNamespace(name=Path(path).name,colorspace_settings=SimpleNamespace(name=''),alpha_mode='')
        bpy=ModuleType('bpy');bpy.app=SimpleNamespace(background=True)
        bpy.data=SimpleNamespace(images=SimpleNamespace(load=load,remove=removed.append))
        with patch.dict(sys.modules,{'bpy':bpy,'babylon_js':package,'babylon_js.ktx_export':ktx,'appearance':appearance}):
            yield written,removed

    def test_default_material_receipt_and_image_paths_are_legacy(self):
        c={'capture':{'views':self.spec}}
        with self.material_runtime() as (written,removed):
            for mode,variant in [('delivery-reference','reference'),('delivery-decoded','decoded')]:
                with delivery_materials(c,[],self.record,mode,self.root) as receipt:
                    self.assertEqual(receipt['variant'],variant)
                    self.assertEqual(receipt['kind'],'local-decoded-delivery-diffuse')
                    self.assertNotIn('accepted',receipt);self.assertNotIn('channel_selection',receipt)
                    self.assertEqual(written[-1][0],self.root/'delivery-images'/('lightmap_'+variant+'.png'))
                    np.testing.assert_array_equal(written[-1][1],np.load(self.record['bindings']['lightmap'][variant+'_array']))
            self.assertEqual(len(removed),2)

    def test_opt_in_materials_load_selected_bytes_and_preserve_existing_outputs(self):
        c={'capture':{'views':self.isolated()}};before=copy.deepcopy(self.record)
        with self.material_runtime() as (written,removed):
            for mode,selection in delivery_channel_plan(c['capture']['views'],self.record).items():
                with delivery_materials(c,[],self.record,mode,self.root) as receipt:
                    self.assertEqual(receipt['kind'],'diagnostic-delivery-channel-isolation')
                    self.assertIs(receipt['accepted'],False);self.assertIs(receipt['source_saved'],False)
                    self.assertEqual(receipt['channel_selection']['lightmap'],selection['lightmap'])
                    binding=receipt['image_bindings'][0]
                    self.assertEqual(binding['array'],self.record['bindings']['lightmap'][selection['lightmap']+'_array'])
                    self.assertEqual(binding['png_sha256'],sha(binding['png']))
                    self.assertEqual((binding['colour_space'],binding['alpha_mode']),('Non-Color','CHANNEL_PACKED'))
                    np.testing.assert_array_equal(written[-1][1],np.load(binding['array']))
            self.assertEqual(len({path for path,_ in written}),4)
            with self.assertRaisesRegex(ValueError,'Preserve existing'):
                with delivery_materials(c,[],self.record,'delivery-reference',self.root):pass
        self.assertEqual(self.record,before)

    def test_diagnostic_image_names_fit_windows_path_limit_with_long_source_stems(self):
        # The real hospital job reached 273 characters although its parent existed.
        # Exercise every mode through the writer boundary, independent of host long-path support.
        self.record['bindings']['lightmap']=self.binding('hospitalRoom_final_v050_codec_palette_v001_combined_agx_rgbd')
        self.record['bindings']['tint']=self.binding('hospitalRoom_final_v050_codec_palette_v001_colour_adjustment')
        stage=self.root/('s'*max(1,140-len(str(self.root))-1));stage.mkdir()
        c={'capture':{'views':self.isolated()}}
        for selection in c['capture']['views']['delivery_channel_isolation']['variants'].values():
            selection['tint']=selection['colour_atlases']
        with self.material_runtime(max_path=260) as (written,removed):
            for mode in delivery_channel_plan(c['capture']['views'],self.record):
                with delivery_materials(c,[],self.record,mode,stage) as receipt:
                    for index,binding in enumerate(receipt['image_bindings']):
                        path=Path(binding['png'])
                        self.assertEqual(path.name,str(index).zfill(3)+'.png')
                        self.assertTrue(path.parent.is_dir())
                        self.assertLess(len(str(path)),260)
                        legacy_name=str(index).zfill(3)+'-'+Path(binding['array']).stem+'.png'
                        if mode.startswith('delivery-isolation-'):
                            self.assertGreaterEqual(len(str(path.parent/legacy_name)),260)
                        self.assertEqual(binding['array'],self.record['bindings'][binding['channel']][binding['variant']+'_array'])
                        self.assertEqual(binding['array_sha256'],sha(binding['array']))
                        self.assertEqual(binding['png_sha256'],sha(path))
            self.assertEqual(len(written),8)
            self.assertEqual(len({path for path,_ in written}),8)
            with self.assertRaisesRegex(ValueError,'Preserve existing'):
                with delivery_materials(c,[],self.record,'delivery-isolation-albedo-decoded',stage):pass


if __name__=='__main__':unittest.main()
