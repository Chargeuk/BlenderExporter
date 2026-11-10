"""Guarded Stage B provenance regression; synthetic assets, no scene/codec claims."""
import copy
import hashlib
import importlib.util
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from PIL import Image

from tests.test_decoded_volume_delivery import ArtifactFixture,container,delivery

ADDON=Path(__file__).resolve().parents[1]/'src/babylon_js'
spec=importlib.util.spec_from_file_location('assembly_validation_fixture',ADDON/'environment_tools/volume_assembly.py')
assembly=importlib.util.module_from_spec(spec);spec.loader.exec_module(assembly)


class AssemblyFixture(ArtifactFixture):
    def __init__(self,base,*,diffuse=False):
        super().__init__(base)
        self.stage=self.base/'stage-a';self.stage.mkdir()
        self.package=self.base/'package';self.package.mkdir()
        companions=['environment.env']
        if diffuse:
            diffuse_recipe,texture=self.prepare_diffuse()
            companions.append(texture['output'])
        self.config=dict(source=self.recipe['source_blend']['path'],source_sha256=self.recipe['source_blend']['sha256'],
            model='room.babylon',volume_export_route=assembly.ROUTE,
            bound_dependencies=[self.ref(Path(self.recipe['receipt']['path'])),self.ref(Path(self.items['simple']['master']['path']))])
        self.config_path=self.write('assembly-config.json',self.config)
        before=copy.deepcopy(self.model);del before['meshes'][0]['metadata']['kadshowEnvironmentLightingVolume']
        assembly.write(self.stage/'room.babylon',before)
        (self.stage/'environment.env').write_bytes(b'converted environment fixture')
        reference=self.receipt['source']['reference'];reference_hash=self.receipt['source']['referenceSha256']
        report=dict(status='passed',options=dict(environment=dict(image=reference)),
            environment=dict(source_sha256=reference_hash),
            delivery_files=[dict(path=str(self.stage/name),sha256=delivery.sha(self.stage/name))
                            for name in ('room.babylon',*companions)])
        if diffuse:report['textures']=[texture]
        assembly.write(self.stage/'room.ktx-report.json',report)
        assembly.write(self.stage/'selection-settings.json',dict(source_sha256=self.config['source_sha256'],
            volume_export_route=assembly.ROUTE,capture_sha256=reference_hash))
        conversion=assembly.verify_stage_a(self.stage,self.config,self.config['source_sha256'])
        assembly.write(self.stage/'STAGE_A_BOUNDARY.json',dict(config_sha256=delivery.sha(self.config_path),
            source_sha256=self.config['source_sha256'],conversion=conversion))
        volume_report=self.report['environment_lighting_volume']
        assembly.write(self.package/'room.babylon',self.model)
        for name in companions:shutil.copy2(self.stage/name,self.package/name)
        for name in ('simple','advanced'):
            old=Path(self.items[name]['ktx']['path']);new=self.package/old.name
            shutil.copy2(old,new);self.decoded[str(new)]=self.decoded[str(old)]
            self.items[name]['ktx']=self.ref(new)
        closure={name:delivery.sha(self.package/name) for name in ('environment.env',
            self.rows['simple']['texture'],self.rows['advanced']['texture'])}
        self.proof=dict(kind='separate-source-volume-assembly-v1',status='assembled',source_saved=False,
            source_sha256=self.config['source_sha256'],config_sha256=delivery.sha(self.config_path),
            stage_a=dict(path=str(self.stage),boundary_sha256=delivery.sha(self.stage/'STAGE_A_BOUNDARY.json'),
                immutable_conversion_report_sha256=delivery.sha(self.stage/'room.ktx-report.json'),model_sha256=conversion['model_sha256']),
            stage_b=dict(api='environment_lighting_volume.export_volume',report=volume_report,
                model_delta='only hasenv.metadata.kadshowEnvironmentLightingVolume'),
            model=str(self.package/'room.babylon'),model_sha256=delivery.sha(self.package/'room.babylon'),closure=closure,
            final_files={p.name:delivery.sha(p) for p in self.package.iterdir()})
        self.proof_path=self.write('assembly-report.json',self.proof)
        self.recipe.update(model=self.ref(self.package/'room.babylon'),conversion_report=self.ref(self.stage/'room.ktx-report.json'),
            assembly=dict(report=self.ref(self.proof_path),config=self.ref(self.config_path)))
        self.write('validation.json',self.recipe)
        if diffuse:
            self.diffuse_recipe_path=self.write('diffuse.json',dict(diffuse_recipe,
                model=self.recipe['model']['path'],conversion_report=self.recipe['conversion_report']['path'],
                assembly=self.recipe['assembly']))

    def prepare_diffuse(self):
        # Use a marker-only RGBD lightmap like the exporter and keep ownership validation real.
        size = 512
        origin = 'top-left'
        filename = 'floor.ktx2'
        marker = 'lightmap_floor'
        master = np.ones((size,size,3),np.float32)
        encoded,_ = delivery.core.encode_rgbd(master)
        master_path = self.base/'lightmap-master.npy'
        albedo = self.base/'albedo.npy'
        prepared = self.base/'lightmap-prepared.png'
        compressed = self.stage/filename
        np.save(master_path,master)
        np.save(albedo,master)
        Image.fromarray(encoded).save(prepared)
        compressed.write_bytes(container(size,size))
        self.decoded[str(self.package/filename)] = encoded
        self.model['meshes'].extend([
            dict(name=marker,metadata=dict(kadshowLightmapEncoding='rgbd-v1',
                kadshowLightmapMinDivisor=int(encoded[...,3].min())/255)),
            dict(name='floor',positions=[0,0,0,1,0,0,0,0,1],indices=[0,1,2]),
        ])

        labels = self.base/'labels.npy'
        native = self.base/'native_island_ids.npy'
        np.save(labels,np.ones((size,size),np.int32))
        native_pixels = np.ones((size,size,4),np.float32)
        np.save(native,native_pixels)
        catalogue = self.write('islands.json',[dict(id=1,object='floor',triangles=[[[0,0],[1,0],[0,1]]])])
        raw_hashes = {}
        for mode in ('direct','indirect'):
            raw = self.base/(mode+'_raw.npy')
            np.save(raw,native_pixels)
            raw_hashes[mode] = delivery.sha(raw)
        source = self.recipe['source_blend']
        geometry_sha256 = hashlib.sha256(b'floor triangle').hexdigest()
        ownership = self.write('ownership.json',dict(method='native-island-id',
            source_sha256=source['sha256'],geometry_sha256=geometry_sha256,
            native_ids_sha256=delivery.sha(native),catalogue_file_sha256=delivery.sha(catalogue),
            raw_sha256=raw_hashes))
        recipe = dict(schema_version=1,source_blend=source['path'],source_sha256=source['sha256'],
            geometry_sha256=geometry_sha256,marker=marker,
            lightmap=dict(ktx=str(self.package/filename),dimensions=[size,size],image_origin=origin,
                master=str(master_path),master_origin=origin,albedo=str(albedo),albedo_origin=origin,
                owned_mask=str(labels),mask_origin=origin,ownership_receipt=str(ownership)))
        texture = dict(output=filename,output_sha256=delivery.sha(compressed),codec='uastc',
            source_path=str(master_path),source_sha256=delivery.sha(master_path),
            prepared=str(prepared),prepared_sha256=delivery.sha(prepared),flip_y=False,mipmaps=False)
        return recipe,texture

    def reseal_assembly(self):
        self.write('assembly-report.json',self.proof)
        self.recipe['assembly']['report']=self.ref(self.proof_path)
        self.write('validation.json',self.recipe)

    def verify(self):
        return delivery.verify_delivery_model(self.package/'room.babylon',self.stage/'room.ktx-report.json',
            self.recipe.get('assembly'),self.base,{})


class GuardedDiffuseAssemblyDeliveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.fixture = AssemblyFixture(temporary.name,diffuse=True)
        self.output = self.fixture.base/'diffuse-validation'

    def validate(self):
        fixture = self.fixture
        with patch.object(delivery.subprocess,'run',side_effect=fixture.run):
            return delivery.validate_delivery(fixture.diffuse_recipe_path,self.output,fixture.ktx)

    def test_full_diffuse_validation_records_guarded_assembly_proof(self):
        fixture = self.fixture
        result = self.validate()
        self.assertEqual(result['kind'],'decoded-delivery-diffuse-contract')
        proof = result['assembly_proof']
        self.assertEqual(proof['stage_a_model_sha256'],delivery.sha(fixture.stage/'room.babylon'))
        self.assertEqual(proof['stage_b_model_sha256'],delivery.sha(fixture.package/'room.babylon'))
        self.assertIn(proof['stage_a_boundary'],result['inputs'])
        comparison = result['bindings']['lightmap']['comparison']
        self.assertGreater(comparison['owned_pixels'],0)
        self.assertEqual(comparison['codec_linear_vs_prepared']['maximum'],0)
        self.assertEqual(delivery.read_json(self.output/'delivery-contract.json'),result)

    def test_diffuse_validation_rejects_file_added_during_decode(self):
        fixture = self.fixture
        original_run = fixture.run

        def add_during_decode(command,**options):
            result = original_run(command,**options)
            if command[1]=='extract':
                (fixture.package/'late-added.bin').write_bytes(b'late addition')
            return result

        fixture.run = add_during_decode
        with self.assertRaisesRegex(ValueError,'final file inventory differs'):
            self.validate()
        self.assertFalse((self.output/'delivery-contract.json').exists())


class GuardedAssemblyDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.fixture=AssemblyFixture(self.temp.name)

    def test_full_volume_validation_uses_original_stage_a_and_guarded_stage_b_report(self):
        fixture=self.fixture;original=(fixture.stage/'room.ktx-report.json').read_bytes()
        result=fixture.validate()
        self.assertEqual(result['status'],'passed')
        proof=result['assembly_proof']
        self.assertNotEqual(proof['stage_a_model_sha256'],proof['stage_b_model_sha256'])
        self.assertEqual((fixture.stage/'room.ktx-report.json').read_bytes(),original)
        self.assertNotIn('environment_lighting_volume',delivery.read_json(fixture.stage/'room.ktx-report.json'))
        self.assertIn(str(fixture.stage/'STAGE_A_BOUNDARY.json'),result['inputs'])

    def test_assembled_model_without_explicit_chain_is_rejected(self):
        fixture=self.fixture;del fixture.recipe['assembly']
        with self.assertRaisesRegex(ValueError,'Actual model differs'):fixture.verify()

    def test_unsealed_assembly_and_config_changes_are_rejected(self):
        fixture=self.fixture
        for path in (fixture.proof_path,fixture.config_path):
            previous=path.read_bytes();path.write_bytes(previous+b' ')
            with self.subTest(path=path),self.assertRaisesRegex(ValueError,'dependency bytes changed'):fixture.verify()
            path.write_bytes(previous)

    def test_resealed_receipt_cannot_authorize_geometry_or_defaults_change(self):
        fixture=self.fixture
        model=delivery.read_json(fixture.package/'room.babylon');model['meshes'][0]['position']=[1,0,0]
        assembly.write(fixture.package/'room.babylon',model)
        digest=delivery.sha(fixture.package/'room.babylon');fixture.proof['model_sha256']=digest
        fixture.proof['final_files']['room.babylon']=digest;fixture.reseal_assembly()
        with self.assertRaisesRegex(ValueError,'outside the volume metadata'):fixture.verify()

    def test_changed_stage_a_env_fails_even_if_assembly_receipt_is_resealed(self):
        fixture=self.fixture;(fixture.stage/'environment.env').write_bytes(b'changed')
        fixture.reseal_assembly()
        with self.assertRaisesRegex(ValueError,'Stage A delivery bytes changed'):fixture.verify()

    def test_copied_stage_a_companion_cannot_be_changed_in_stage_b(self):
        fixture=self.fixture;(fixture.package/'environment.env').write_bytes(b'changed')
        digest=delivery.sha(fixture.package/'environment.env')
        fixture.proof['final_files']['environment.env']=digest;fixture.proof['closure']['environment.env']=digest
        fixture.reseal_assembly()
        with self.assertRaisesRegex(ValueError,'dependency bytes changed'):fixture.verify()

    def test_closure_volume_record_and_inventory_mismatch_reject(self):
        fixture=self.fixture;original=copy.deepcopy(fixture.proof)
        for change in ('closure','volume','inventory','api'):
            fixture.proof=copy.deepcopy(original)
            if change=='closure':fixture.proof['closure']['environment.env']='0'*64
            elif change=='volume':fixture.proof['stage_b']['report']['textureSha256']='0'*64
            elif change=='inventory':fixture.proof['final_files'].pop('environment.env')
            else:fixture.proof['stage_b']['api']='unverified alternate API'
            fixture.reseal_assembly()
            with self.subTest(change=change),self.assertRaises(ValueError):fixture.verify()

    def test_source_and_boundary_digests_are_required(self):
        fixture=self.fixture;original=copy.deepcopy(fixture.proof)
        for change in ('source','boundary','conversion'):
            fixture.proof=copy.deepcopy(original)
            if change=='source':fixture.proof['source_sha256']='0'*64
            elif change=='boundary':fixture.proof['stage_a']['boundary_sha256']='0'*64
            else:fixture.proof['stage_a']['immutable_conversion_report_sha256']='0'*64
            fixture.reseal_assembly()
            with self.subTest(change=change),self.assertRaises(ValueError):fixture.verify()

    def test_standalone_original_conversion_route_remains_supported(self):
        fixture=self.fixture
        proof,_=delivery.verify_delivery_model(fixture.stage/'room.babylon',fixture.stage/'room.ktx-report.json',None,fixture.base,{})
        self.assertIsNone(proof)

    def test_file_added_during_decode_fails_completion_inventory(self):
        fixture=self.fixture;original=fixture.run
        def add_during_decode(command,**options):
            result=original(command,**options)
            if command[1]=='extract':(fixture.package/'late-added.bin').write_bytes(b'late addition')
            return result
        fixture.run=add_during_decode
        with self.assertRaisesRegex(ValueError,'final file inventory differs'):fixture.validate()

    def test_model_changed_between_parse_and_verifier_is_rejected_on_both_routes(self):
        fixture=self.fixture;model=fixture.package/'room.babylon';original=model.read_bytes()
        real_verify=delivery.verify_delivery_model
        def change_after_parse(*args,**kwargs):
            model.write_bytes(original+b' ')
            return real_verify(*args,**kwargs)
        diffuse=fixture.write('diffuse.json',dict(schema_version=1,conversion_report=fixture.recipe['conversion_report']['path'],
            model=str(model),assembly=fixture.recipe['assembly']))
        for route in ('volume','diffuse'):
            model.write_bytes(original)
            with self.subTest(route=route),patch.object(delivery,'verify_delivery_model',side_effect=change_after_parse):
                with self.assertRaisesRegex(ValueError,'identity was bound'):
                    if route=='volume':fixture.validate()
                    else:delivery.validate_delivery(diffuse,fixture.base/'diffuse-validation',fixture.ktx)
        model.write_bytes(original)

    def test_json_parser_and_hash_are_bound_to_the_same_bytes(self):
        fixture=self.fixture;path=fixture.package/'room.babylon';inputs={}
        parsed=delivery.bound_json(path,inputs)
        self.assertEqual(parsed,fixture.model)
        self.assertEqual(inputs[str(path)],delivery.sha(path))
        path.write_bytes(path.read_bytes()+b' ')
        with self.assertRaisesRegex(ValueError,'identity was bound'):delivery.bound_json(path,inputs)

    def test_bound_receipt_master_and_other_dependency_changes_are_rejected(self):
        fixture=self.fixture
        other=fixture.base/'other.bin';other.write_bytes(b'bound source input')
        fixture.config['bound_dependencies'].append(self.fixture.ref(other))
        fixture.write('assembly-config.json',fixture.config)
        # Update the fixture boundary because the fixture has a new genuine bound dependency.
        boundary=assembly.read(fixture.stage/'STAGE_A_BOUNDARY.json');boundary['config_sha256']=delivery.sha(fixture.config_path)
        assembly.write(fixture.stage/'STAGE_A_BOUNDARY.json',boundary)
        fixture.proof['config_sha256']=delivery.sha(fixture.config_path)
        fixture.proof['stage_a']['boundary_sha256']=delivery.sha(fixture.stage/'STAGE_A_BOUNDARY.json')
        fixture.recipe['assembly']['config']=fixture.ref(fixture.config_path);fixture.reseal_assembly()
        for path in (Path(fixture.recipe['receipt']['path']),Path(fixture.items['simple']['master']['path']),other,
                     Path(fixture.items['advanced']['master']['path'])):
            original=path.read_bytes();path.write_bytes(original+b'changed')
            with self.subTest(path=path),self.assertRaises(ValueError):fixture.verify()
            path.write_bytes(original)

    def test_unbound_receipt_is_rejected_even_with_valid_model_and_master(self):
        fixture=self.fixture;fixture.config['bound_dependencies']=fixture.config['bound_dependencies'][1:]
        fixture.write('assembly-config.json',fixture.config)
        boundary=assembly.read(fixture.stage/'STAGE_A_BOUNDARY.json');boundary['config_sha256']=delivery.sha(fixture.config_path)
        assembly.write(fixture.stage/'STAGE_A_BOUNDARY.json',boundary)
        fixture.proof['config_sha256']=delivery.sha(fixture.config_path)
        fixture.proof['stage_a']['boundary_sha256']=delivery.sha(fixture.stage/'STAGE_A_BOUNDARY.json')
        fixture.recipe['assembly']['config']=fixture.ref(fixture.config_path);fixture.reseal_assembly()
        with self.assertRaisesRegex(ValueError,'receipt requires hash-bound'):fixture.verify()

    def test_diffuse_remember_cannot_replace_model_hash_through_source_alias(self):
        fixture=self.fixture;model=fixture.stage/'room.babylon';original=model.read_bytes();changed=original+b' '
        recipe=fixture.write('diffuse-alias.json',dict(schema_version=1,
            conversion_report=str(fixture.stage/'room.ktx-report.json'),model=str(model),source_blend=str(model),
            source_sha256=hashlib.sha256(changed).hexdigest()))
        real_verify=delivery.verify_delivery_model
        def change_after_verify(*args,**kwargs):
            result=real_verify(*args,**kwargs);model.write_bytes(changed);return result
        with patch.object(delivery,'verify_delivery_model',side_effect=change_after_verify):
            with self.assertRaisesRegex(ValueError,'identity was bound'):
                delivery.validate_delivery(recipe,fixture.base/'diffuse-alias-output',fixture.ktx)

    def test_volume_remember_cannot_replace_earlier_artifact_hash_through_alias(self):
        fixture=self.fixture;model=fixture.package/'room.babylon';changed=model.read_bytes()+b' '
        fixture.recipe['source_blend']=dict(path=str(model),sha256=hashlib.sha256(changed).hexdigest())
        fixture.write('validation.json',fixture.recipe)
        real_sha=delivery.sha;calls=0
        def change_before_second_read(path):
            nonlocal calls
            if Path(path).resolve()==model:
                calls+=1
                if calls==2:model.write_bytes(changed)
            return real_sha(path)
        with patch.object(delivery,'sha',side_effect=change_before_second_read):
            with self.assertRaisesRegex(ValueError,'identity was bound'):fixture.validate()

    def test_recipe_hash_records_parsed_bytes_and_detects_change_before_completion(self):
        fixture=self.fixture;real_read=Path.read_bytes;changed=False
        def change_after_read(path):
            nonlocal changed
            data=real_read(path)
            if path.resolve()==fixture.recipe_path and not changed:
                changed=True;path.write_bytes(data+b' ')
            return data
        with patch.object(Path,'read_bytes',change_after_read):
            with self.assertRaisesRegex(ValueError,'Volume input changed during validation'):fixture.validate()


if __name__=='__main__':unittest.main()
