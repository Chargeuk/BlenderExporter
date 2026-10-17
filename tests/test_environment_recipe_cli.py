"""Shared preparation defaults and fresh candidate sizing; no Blender import required."""
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

MODULE=Path(__file__).resolve().parents[1]/'src/babylon_js/environment_tools/recipe_cli.py'
spec=importlib.util.spec_from_file_location('installed_recipe_contract',MODULE)
recipe=importlib.util.module_from_spec(spec);spec.loader.exec_module(recipe)


class ExportPreparationDefaultsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()

    def test_default_export_has_no_region_dependency_or_colour_requirement(self):
        kwargs,checks=recipe.export_conversion_settings(self.root,{},None)
        self.assertNotIn('ktx_lightmap_region_policy',kwargs)
        self.assertNotIn('colour_adjustment_size',kwargs)
        self.assertFalse(kwargs['colour_adjustment_enabled'])
        self.assertEqual(checks,dict(regions=False,colour=False,colour_codec=None))

    def test_colour_inherits_material_codec_and_can_be_explicitly_disabled(self):
        for global_codec in ('basis-lz','uastc'):
            config=dict(preparation=dict(operator_settings=dict(ktx_codec=global_codec,colour_adjustment_codec='INHERIT')))
            kwargs,checks=recipe.export_conversion_settings(self.root,config,dict(strength=.6))
            self.assertTrue(kwargs['colour_adjustment_enabled'])
            self.assertEqual(checks['colour_codec'],global_codec)
            self.assertFalse(checks['regions'])
        config['preparation']['operator_settings']['colour_adjustment_enabled']=False
        kwargs,checks=recipe.export_conversion_settings(self.root,config,dict(strength=.6))
        self.assertFalse(kwargs['colour_adjustment_enabled']);self.assertIsNone(checks['colour_codec'])

    def test_optional_region_and_independent_colour_opt_in(self):
        (self.root/'regions.json').write_text('{}')
        config=dict(rgbd_region_policy='regions.json',colour_adjustment_size='1024',
            preparation=dict(operator_settings=dict(ktx_codec='basis-lz',colour_adjustment_codec='uastc')))
        kwargs,checks=recipe.export_conversion_settings(self.root,config,dict(strength=1))
        self.assertEqual(kwargs['ktx_lightmap_region_policy'],str(self.root/'regions.json'))
        self.assertEqual(kwargs['colour_adjustment_size'],'1024')
        self.assertEqual(checks,dict(regions=True,colour=True,colour_codec='uastc'))
        config.update(rgbd_region_policy=False,fixture_rgbd_options='missing-legacy-policy.json')
        kwargs,checks=recipe.export_conversion_settings(self.root,config,dict(strength=1))
        self.assertEqual(kwargs['ktx_lightmap_region_policy'],'');self.assertFalse(checks['regions'])

    def test_report_validation_uses_requested_codec_and_optional_proofs(self):
        config=self.root/'export.json';stage=self.root/'stage';stage.mkdir()
        recipe.write(config,dict(model='fixture.babylon',source_sha256='source-identity'))
        recipe.write(stage/'selection-settings.json',dict(source_sha256='source-identity'))
        report=stage/'fixture.ktx-report.json'
        recipe.write(report,dict(status='passed',textures=[]))
        recipe.verify_integrated_stage(config,stage)
        with self.assertRaisesRegex(ValueError,'region RGBD'):recipe.verify_integrated_stage(config,stage,regions=True)
        texture=stage/'fixture_colour_adjustment.ktx2';texture.write_bytes(b'controlled-colour-texture')
        recipe.write(report,dict(status='passed',textures=[dict(output=texture.name,output_sha256=recipe.sha(texture),codec='basis-lz',mipmaps=False)]))
        recipe.verify_integrated_stage(config,stage,colour=True,expected_colour_codec='basis-lz')
        with self.assertRaisesRegex(ValueError,'requested codec'):
            recipe.verify_integrated_stage(config,stage,colour=True,expected_colour_codec='uastc')


class CandidateBindingTests(unittest.TestCase):
    def make_candidate(self,root,bake_size):
        retained=root/'retained';retained.mkdir();build=root/'build';build.mkdir();physical=root/'physical';physical.mkdir()
        source=retained/'source.blend';source.write_bytes(b'retained-source')
        snapshot=physical/'physical.blend';snapshot.write_bytes(b'disposable-physical-source')
        recipe.write(physical/'environment-build.json',dict(size=bake_size))
        recipe.write(physical/'REQUIRES_FULL_BAKE.json',dict(source_sha256=recipe.sha(source),snapshot_sha256=recipe.sha(snapshot),geometry_sha256='matched-geometry'))
        files={'masters/combined.exr':b'new-linear-combined','masters/albedo_uv.exr':b'new-albedo',
            'ownership/labels.npy':b'new-matched-ownership','capture/capture.exr':b'new-capture'}
        for name,data in files.items():
            path=build/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
        for name,data in {'geometry/result.json':dict(geometry_sha256='matched-geometry'),
            'masters/combined.provenance.json':dict(combined_sha256=recipe.sha(build/'masters/combined.exr')),
            'bake/islands.json':[dict(id=1,object='fixture')]}.items():
            path=build/name;path.parent.mkdir(parents=True,exist_ok=True);recipe.write(path,data)
        stages={key:dict(outputs={}) for key in ('geometry','bake','ownership','combine','capture')}
        for stage,names in {'geometry':['geometry/result.json'],'bake':['bake/islands.json','masters/albedo_uv.exr'],
            'ownership':['ownership/labels.npy'],'combine':['masters/combined.exr','masters/combined.provenance.json'],'capture':['capture/capture.exr']}.items():
            stages[stage]['outputs']={name:recipe.sha(build/name) for name in names}
        recipe.write(build/'build.json',dict(stages=stages,inputs={str(snapshot):recipe.sha(snapshot)}))
        recipe.write(retained/'capture-association.json',dict(capture=dict(path='capture.exr',sha256='old'),associated_source=dict(sha256=recipe.sha(source))))
        recipe.write(retained/'regions.json',dict(schema_version=1,encoding='rgbd-v1',selection=dict(ids=[1])))
        config=retained/'export.json'
        recipe.write(config,dict(source='source.blend',source_sha256=recipe.sha(source),lightmap_size='1024',capture_association='capture-association.json',
            fixture_rgbd_options='regions.json',preparation=dict(candidate_outputs={'old-combined.exr':'masters/combined.exr','capture.exr':'capture/capture.exr'}),
            source_geometry_association=dict(source_geometry='missing-old-source-geometry.json'),
            bound_dependencies=[dict(path='old-combined.exr',sha256='old-accepted-combined')]))
        return config,build,physical

    def test_preview_and_final_candidate_sizes_keep_matched_ownership(self):
        for baked,expected in ((512,512),(2048,1024)):
            with self.subTest(bake_size=baked),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);config,build,_=self.make_candidate(root,baked);out=root/'candidate-export.json'
                recipe.bind_candidate(config,SimpleNamespace(build_candidate=build,out=out))
                bound=recipe.read(out);region=recipe.read(root/'candidate-export.rgbd-regions.json')
                proof=recipe.check_source_geometry_association(config.parent,bound,bound['source_sha256'])
                self.assertEqual(proof['mode'],'fresh_candidate')
                self.assertEqual(bound['lightmap_size'],str(expected));self.assertEqual(region['size'],expected)
                self.assertLessEqual(bound['candidate_delivery']['output_size'],bound['candidate_delivery']['bake_size'])
                self.assertEqual(region['ownership_labels'],str(build/'ownership/labels.npy'))
                self.assertEqual(bound['bound_dependencies'][0]['sha256'],recipe.sha(build/'masters/combined.exr'))
                self.assertEqual(recipe.read(config)['lightmap_size'],'1024')
                self.assertEqual(recipe.read(config)['bound_dependencies'][0]['sha256'],'old-accepted-combined')
                region['geometry_sha256']='different-but-self-consistent-region-geometry'
                other_geometry=root/'region-geometry.json';recipe.write(other_geometry,dict(geometry_sha256=region['geometry_sha256']))
                region['geometry_reference']=str(other_geometry);region['geometry_reference_sha256']=recipe.sha(other_geometry)
                region_path=root/'candidate-export.rgbd-regions.json';recipe.write(region_path,region)
                for row in bound['input_bindings']:
                    if Path(row['source'])==region_path:row['sha256']=recipe.sha(region_path)
                with self.assertRaisesRegex(ValueError,'Region geometry differs'):
                    recipe.check_source_geometry_association(config.parent,bound,bound['source_sha256'])

    def test_candidate_geometry_must_match_preparation_receipt(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);config,build,physical=self.make_candidate(root,512)
            receipt=recipe.read(physical/'REQUIRES_FULL_BAKE.json');receipt['geometry_sha256']='different'
            recipe.write(physical/'REQUIRES_FULL_BAKE.json',receipt)
            with self.assertRaisesRegex(ValueError,'geometry differs'):
                recipe.bind_candidate(config,SimpleNamespace(build_candidate=build,out=root/'candidate.json'))
            self.assertFalse((root/'candidate.json').exists())

class SourceGeometryAssociationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.geometry=self.root/'provenance/geometry.json';self.geometry.parent.mkdir()
        recipe.write(self.geometry,dict(geometry_sha256='accepted-geometry'))
        self.identity=self.root/'provenance/source-geometry.json'
        self.association=dict(associated_source_sha256='retained-source',uv_comparison=dict(unchanged=True),
            geometry_normals_transforms_parents_groups_exact=True,
            geometry_reference=dict(path='provenance/geometry.json',sha256=recipe.sha(self.geometry)))
        recipe.write(self.identity,self.association)
        self.config=dict(source_geometry_association=dict(source_geometry='provenance/source-geometry.json'))
    def tearDown(self):self.temp.cleanup()

    def test_accepted_association_and_optional_unrelated_scene(self):
        proof=recipe.check_source_geometry_association(self.root,self.config,'retained-source')
        self.assertEqual(proof['mode'],'accepted_maps')
        self.assertEqual(proof['geometry_reference_sha256'],recipe.sha(self.geometry))
        self.assertIsNone(recipe.check_source_geometry_association(self.root,{},'another-source'))

    def test_reject_changed_source_uv_or_geometry_association(self):
        for field in ('source','uv','geometry_exact','geometry_hash'):
            with self.subTest(field=field):
                value=json.loads(json.dumps(self.association))
                if field=='source':value['associated_source_sha256']='another-source'
                elif field=='uv':value['uv_comparison']['unchanged']=False
                elif field=='geometry_exact':value['geometry_normals_transforms_parents_groups_exact']=False
                else:value['geometry_reference']['sha256']='stale-reference-hash'
                recipe.write(self.identity,value)
                with self.assertRaises(ValueError):recipe.check_source_geometry_association(self.root,self.config,'retained-source')

    def test_region_geometry_must_match_the_actual_associated_geometry(self):
        region_path=self.root/'regions.json';self.config['rgbd_region_policy']='regions.json'
        recipe.write(region_path,dict(geometry_sha256='accepted-geometry'))
        self.assertEqual(recipe.check_source_geometry_association(self.root,self.config,'retained-source')['geometry_sha256'],'accepted-geometry')
        # Both records can be internally consistent yet describe different geometry.
        other=self.root/'other-geometry.json';recipe.write(other,dict(geometry_sha256='other-geometry'))
        recipe.write(region_path,dict(geometry_reference=str(other),geometry_sha256='other-geometry'))
        with self.assertRaisesRegex(ValueError,'Region geometry differs'):
            recipe.check_source_geometry_association(self.root,self.config,'retained-source')

    def test_geometry_bytes_remain_guarded_after_association(self):
        self.geometry.write_text('changed geometry')
        with self.assertRaisesRegex(ValueError,'geometry reference changed'):
            recipe.check_source_geometry_association(self.root,self.config,'retained-source')


class CompatibilityVerifierTests(unittest.TestCase):
    def make_stage(self,root,regions,source_colour,global_codec='basis-lz',colour_codec='INHERIT',enabled=True):
        config=root/'export.json';stage=root/'stage';stage.mkdir()
        data=dict(model='fixture.babylon',source_sha256='retained-source',preparation=dict(operator_settings=dict(
            ktx_codec=global_codec,colour_adjustment_codec=colour_codec,colour_adjustment_enabled=enabled)))
        rows=[]
        def texture(name,**fields):
            path=stage/name;path.write_bytes(name.encode())
            rows.append(dict(output=name,output_sha256=recipe.sha(path),**fields))
        if regions:
            data['rgbd_region_policy']='regions.json';recipe.write(root/'regions.json',dict(schema_version=1))
            texture('lighting.ktx2',encoding='rgbd-v1',rgbd_region=dict(outside_decoded_exact=True,policy_sha256=recipe.sha(root/'regions.json')))
        if source_colour and enabled:
            actual=global_codec if colour_codec=='INHERIT' else colour_codec
            texture('fixture_colour_adjustment.ktx2',codec=actual,mipmaps=False)
        recipe.write(config,data)
        recipe.write(stage/'selection-settings.json',dict(source_sha256='retained-source',colour_adjustment_enabled=source_colour,colour_adjustment_strength=.7))
        recipe.write(stage/'fixture.ktx-report.json',dict(status='passed',textures=rows))
        return config,stage

    def test_verify_colour_follows_enabled_features_and_requested_codec(self):
        cases=[(False,False,'basis-lz','INHERIT',True),(False,True,'basis-lz','INHERIT',True),
               (False,True,'uastc','INHERIT',True),(True,True,'basis-lz','INHERIT',True),
               (True,True,'basis-lz','uastc',True),(True,False,'basis-lz','INHERIT',True),
               (False,True,'basis-lz','uastc',False)]
        for case in cases:
            with self.subTest(case=case),tempfile.TemporaryDirectory() as temp:
                config,stage=self.make_stage(Path(temp),*case)
                self.assertEqual(recipe.verify_colour_stage(config,stage)['status'],'passed')

    def test_verify_regions_options_must_match_configured_and_exported_policy(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);config,stage=self.make_stage(root,True,False)
            recipe.main('verify-regions',config,['--stage',str(stage),'--options',str(root/'regions.json')])
            other=root/'other.json';recipe.write(other,dict(schema_version=2))
            with self.assertRaisesRegex(ValueError,'--options must match'):
                recipe.main('verify-regions',config,['--stage',str(stage),'--options',str(other)])
            recipe.write(root/'regions.json',dict(schema_version=2))
            with self.assertRaisesRegex(ValueError,'region policy identity differs'):
                recipe.verify_colour_stage(config,stage)

    def test_sha_streams_without_read_bytes_and_preserves_standard_digest(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'bytes';path.write_bytes(b'abc')
            with patch.object(Path,'read_bytes',side_effect=AssertionError('whole-file read')):
                self.assertEqual(recipe.sha(path),'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')

class StageInputIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.stage=self.root/'stage';self.stage.mkdir()
        self.combined=self.root/'combined.exr';self.combined.write_bytes(b'original-linear-combined')
        self.capture=self.root/'capture.exr';self.capture.write_bytes(b'current-capture')
        self.config=self.root/'export.json'
        self.options=dict(model='fixture.babylon',source_sha256='retained-source',expected_combined_sha256=recipe.sha(self.combined),
            capture_association='capture-association.json',bound_dependencies=[dict(path='combined.exr',sha256=recipe.sha(self.combined))])
        recipe.write(self.root/'capture-association.json',dict(capture=dict(path='capture.exr',sha256=recipe.sha(self.capture))))
        self.selection=dict(source_sha256='retained-source',lightmap=str(self.combined),capture=str(self.capture),capture_sha256=recipe.sha(self.capture))
        # Existing receipts have no lightmap_sha256; this is the real pre-AgX source report shape.
        self.report=dict(status='passed',textures=[],options=dict(agx_report=dict(source=str(self.combined),source_sha256=recipe.sha(self.combined))))
        self.save()
    def tearDown(self):self.temp.cleanup()
    def save(self):
        recipe.write(self.config,self.options);recipe.write(self.stage/'selection-settings.json',self.selection)
        recipe.write(self.stage/'fixture.ktx-report.json',self.report)
    def verify(self):return recipe.verify_integrated_stage(self.config,self.stage)

    def test_real_legacy_agx_and_raw_lightmap_reports_prove_current_combined(self):
        self.assertEqual(self.verify()['status'],'passed')
        texture=self.stage/'lighting.ktx2';texture.write_bytes(b'gpu-texture')
        self.report=dict(status='passed',textures=[dict(output=texture.name,output_sha256=recipe.sha(texture),encoding='rgbd-v1',
            lightmap_markers=['lightmap_fixture'],source_path=str(self.combined),source_sha256=recipe.sha(self.combined))])
        companion=self.stage/'companion.ktx2';companion.write_bytes(b'unrelated-rgbd-companion')
        self.report['textures'].append(dict(output=companion.name,output_sha256=recipe.sha(companion),
            encoding='rgbd-v1',source_path=str(self.root/'companion.exr'),source_sha256='unrelated-companion-digest'))
        self.save();self.assertEqual(self.verify()['status'],'passed')

    def test_stale_real_capture_field_is_rejected(self):
        self.selection['capture_sha256']='previous-capture';self.save()
        with self.assertRaisesRegex(ValueError,'stage capture identity differs'):self.verify()

    def test_identical_lightmap_and_capture_copies_preserve_content_identity(self):
        other=self.root/'other.exr';other.write_bytes(self.combined.read_bytes())
        other_capture=self.root/'other-capture.exr';other_capture.write_bytes(self.capture.read_bytes())
        self.selection.update(lightmap=str(other),capture=str(other_capture));self.save()
        # The original export report remains unchanged when only source locations move.
        self.assertEqual(self.verify()['status'],'passed')

    def test_different_byte_lightmap_copy_is_rejected(self):
        other=self.root/'different.exr';other.write_bytes(b'other-linear-lighting')
        self.selection['lightmap']=str(other);self.save()
        with self.assertRaisesRegex(ValueError,'lightmap input changed'):self.verify()

    def test_overwritten_lightmap_cannot_inherit_old_export_proof(self):
        self.combined.write_bytes(b'new-linear-combined')
        self.options['expected_combined_sha256']=recipe.sha(self.combined)
        self.options['bound_dependencies'][0]['sha256']=recipe.sha(self.combined);self.save()
        with self.assertRaisesRegex(ValueError,'combined input identity differs'):self.verify()

    def test_unknown_legacy_identity_is_rejected_but_optional_inputs_stay_optional(self):
        self.report.pop('options');self.save()
        with self.assertRaisesRegex(ValueError,'no durable combined input identity'):self.verify()
        self.options.pop('expected_combined_sha256');self.options.pop('capture_association');self.save()
        self.assertEqual(self.verify()['status'],'passed')

    def test_current_candidate_bindings_resolve_combined_and_capture_inputs(self):
        moved=self.root/'candidate';moved.mkdir()
        new_combined=moved/'combined.exr';new_capture=moved/'capture.exr'
        new_combined.write_bytes(self.combined.read_bytes());new_capture.write_bytes(self.capture.read_bytes())
        self.options['input_bindings']=[dict(path='combined.exr',source=str(new_combined),sha256=recipe.sha(new_combined)),
            dict(path='capture.exr',source=str(new_capture),sha256=recipe.sha(new_capture))]
        self.selection.update(lightmap=str(new_combined),capture=str(new_capture))
        self.report['options']['agx_report']['source']=str(new_combined);self.save()
        self.assertEqual(self.verify()['status'],'passed')


if __name__=='__main__':unittest.main()
