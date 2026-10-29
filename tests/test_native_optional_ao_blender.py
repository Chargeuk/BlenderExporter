"""Real managed-graph regression fixture; background Blender only, no rendering."""
import argparse,hashlib,importlib.util,json,sys,unittest
from pathlib import Path
import bpy

parser=argparse.ArgumentParser()
parser.add_argument('--addon-source',type=Path,required=True)
parser.add_argument('--output',type=Path,required=True)
parser.add_argument('--require-registered',action='store_true')
args=parser.parse_args(sys.argv[sys.argv.index('--')+1:])
assert bpy.app.background and not args.output.exists()
args.output.mkdir(parents=True)
sys.path.insert(0,str(args.addon_source.parent))
sys.path.insert(0,str(args.addon_source/'environment_tools'))
from babylon_js.lighting_controls import create_preview_material
import babylon_js
assert Path(babylon_js.__file__).resolve().parent==args.addon_source.resolve()
if args.require_registered:
    assert bpy.ops.export.bjs.get_rna_type().identifier
    assert all(key in bpy.types.World.bl_rna.properties for key in ['positionsPrecision','normalsPrecision','UVsPrecision','exportTangents','usePBRMaterials'])
from appearance import preview_identity
spec=importlib.util.spec_from_file_location('tested_native_views',args.addon_source/'workflow_tools/native_view_diagnostics.py')
native=importlib.util.module_from_spec(spec);spec.loader.exec_module(native)

def nodes(material):
    result=[];seen=set()
    def visit(tree):
        if tree.as_pointer() in seen:return
        seen.add(tree.as_pointer())
        for n in tree.nodes:
            result.append(n)
            if n.type=='GROUP' and n.node_tree:visit(n.node_tree)
    visit(material.node_tree);return result

def role_nodes(material,role):
    return [n for n in nodes(material) if n.type=='TEX_IMAGE' and n.get('bjs_preview_image_role')==role]

class NativeOptionalAOTests(unittest.TestCase):
    def setUp(self):
        self.directory=args.output/hashlib.sha256(self._testMethodName.encode()).hexdigest()[:8];self.directory.mkdir()
        self.scene=bpy.data.scenes.new(self._testMethodName)
        self.scene.render.engine='CYCLES'
        def image(name,colour,colourspace):
            path=self.directory/(name+('.png' if colourspace=='sRGB' else '.exr'))
            im=bpy.data.images.new(name,8,8,alpha=True,float_buffer=True)
            im.colorspace_settings.name=colourspace
            im.pixels.foreach_set(list(colour)*64)
            im.filepath_raw=str(path);im.file_format='PNG' if colourspace=='sRGB' else 'OPEN_EXR'
            im.save();bpy.data.images.remove(im)
            im=bpy.data.images.load(str(path),check_existing=False);im.colorspace_settings.name=colourspace
            return im
        self.image=image
        colour=image('colour',(.3,.4,.5,1),'sRGB')
        direct=image('source_direct',(.2,.2,.2,1),'Linear Rec.709')
        indirect=image('source_indirect',(.8,.8,.8,1),'Linear Rec.709')
        ids=image('islands',(1,1,1,1),'Non-Color')
        self.material,self.controls=create_preview_material(colour,direct,indirect,ids,name=self._testMethodName,scene=self.scene)
        self.maps={}
        for role,value in [('direct',.5),('indirect',.125)]:
            self.maps[role]=self.image('replacement_'+role,(value,value,value,1),'Linear Rec.709').filepath
        self.available=next(n for n in nodes(self.material) if n.get('bjs_ao_available'))
        self.assertIsNone(role_nodes(self.material,'ao')[0].image)
        self.assertEqual(self.available.outputs[0].default_value,0)
        self.assertEqual(self.controls.inputs['AO Strength'].default_value,0)

    def check_native(self,variant):
        before=preview_identity(self.material)
        counts=(len(bpy.data.materials),len(bpy.data.node_groups),len(bpy.data.images))
        with native.preview_variant(self.material,self.directory,variant,native_maps=self.maps) as (clone,record):
            self.assertIsNone(role_nodes(clone,'ao')[0].image)
            self.assertEqual(record['mode'],'native-transport')
            self.assertEqual(len(record['preserved_unassigned_optional_samplers']),1)
            preserved=record['preserved_unassigned_optional_samplers'][0]
            self.assertEqual((preserved['role'],preserved['image'],preserved['availability']),('ao',None,0))
            self.assertTrue(preserved['preserved'])
            self.assertEqual(record['original_actual_controls'],record['clone_actual_controls'])
            for role in ('direct','indirect'):
                self.assertTrue(all(Path(n.image.filepath)==Path(self.maps[role]) for n in role_nodes(clone,role)))
            for role in ('colour','island_ids'):
                original=role_nodes(self.material,role)
                changed=role_nodes(clone,role)
                for a,b in zip(original,changed):
                    self.assertEqual(hashlib.sha256(Path(a.image.filepath).read_bytes()).hexdigest(),hashlib.sha256(Path(b.image.filepath).read_bytes()).hexdigest())
                    self.assertEqual((a.interpolation,a.extension),(b.interpolation,b.extension))
        self.assertEqual(preview_identity(self.material),before)
        self.assertEqual((len(bpy.data.materials),len(bpy.data.node_groups),len(bpy.data.images)),counts)

    def test_raw_and_processed_preserve_disabled_none_AO_and_restore_source(self):
        for variant in ('raw','processed'):
            with self.subTest(variant=variant):self.check_native(variant)

    def test_unavailable_AO_remains_neutral_with_nonzero_saved_strength(self):
        self.controls.inputs['AO Strength'].default_value=.7
        for variant in ('raw','processed'):
            with self.subTest(variant=variant):self.check_native(variant)

    def test_required_missing_colour_DI_and_ID_images_still_fail(self):
        for role in ('colour','direct','indirect','island_ids'):
            with self.subTest(role=role):
                node=role_nodes(self.material,role)[0];image=node.image;node.image=None
                try:
                    with self.assertRaises(ValueError):
                        with native.preview_variant(self.material,self.directory,'raw',native_maps=self.maps):pass
                    self.assertIsNone(node.image)
                finally:node.image=image

    def test_active_AO_without_image_still_fails(self):
        self.available.outputs[0].default_value=1
        with self.assertRaisesRegex(ValueError,'Active AO has no image'):
            with native.preview_variant(self.material,self.directory,'raw',native_maps=self.maps):pass

    def test_malformed_missing_or_duplicate_availability_still_fails(self):
        tree=next(n.node_tree for n in self.material.node_tree.nodes if n.type=='GROUP' and n.get('bjs_lighting_controls'))
        self.available['bjs_ao_available']=0
        with self.assertRaises(ValueError):
            with native.preview_variant(self.material,self.directory,'raw',native_maps=self.maps):pass
        self.available['bjs_ao_available']=1
        duplicate=tree.nodes.new('ShaderNodeValue');duplicate['bjs_ao_available']=1;duplicate.outputs[0].default_value=0
        with self.assertRaises(ValueError):
            with native.preview_variant(self.material,self.directory,'processed',native_maps=self.maps):pass

    def test_assigned_AO_image_is_preserved_and_freshly_bound(self):
        ao=self.image('assigned_AO',(.6,.6,.6,1),'Non-Color')
        role_nodes(self.material,'ao')[0].image=ao;self.available.outputs[0].default_value=1
        self.controls.inputs['AO Strength'].default_value=.8;before=preview_identity(self.material)
        for variant in ('raw','processed'):
            with native.preview_variant(self.material,self.directory,variant,native_maps=self.maps) as (clone,record):
                assigned=role_nodes(clone,'ao')[0].image
                self.assertIsNotNone(assigned);self.assertEqual(assigned.colorspace_settings.name,'Non-Color')
                self.assertEqual(Path(assigned.filepath),Path(ao.filepath))
                self.assertEqual(record['preserved_unassigned_optional_samplers'],[])
                self.assertIn('ao',[row['role'] for row in record['bindings'].values()])
            self.assertEqual(preview_identity(self.material),before)

    def test_missing_replacement_and_wrong_interpretation_still_fail(self):
        wrong=dict(self.maps,direct=str(self.directory/'MISSING.exr'))
        with self.assertRaisesRegex(ValueError,'Actual managed diagnostic image missing'):
            with native.preview_variant(self.material,self.directory,'raw',native_maps=wrong):pass
        for role,space in [('direct','sRGB'),('colour','Non-Color'),('island_ids','sRGB')]:
            with self.subTest(role=role):
                node=role_nodes(self.material,role)[0];previous=node.image.colorspace_settings.name
                node.image.colorspace_settings.name=space
                try:
                    with self.assertRaisesRegex(ValueError,'Unexpected native sampler interpretation'):
                        with native.preview_variant(self.material,self.directory,'raw',native_maps=self.maps):pass
                finally:node.image.colorspace_settings.name=previous

    def test_failed_native_binding_restores_images_groups_and_source_identity(self):
        before=preview_identity(self.material)
        counts=(len(bpy.data.materials),len(bpy.data.node_groups),len(bpy.data.images))
        wrong=dict(self.maps,indirect=str(self.directory/'MISSING.exr'))
        with self.assertRaisesRegex(ValueError,'Actual managed diagnostic image missing'):
            with native.preview_variant(self.material,self.directory,'processed',native_maps=wrong):
                self.fail('Missing required replacement must never yield')
        self.assertEqual(preview_identity(self.material),before)
        self.assertEqual((len(bpy.data.materials),len(bpy.data.node_groups),len(bpy.data.images)),counts)
        self.assertIsNone(role_nodes(self.material,'ao')[0].image)

    def test_legacy_response_variants_keep_their_DI_binding_contract(self):
        masters=self.directory/'masters';masters.mkdir()
        import shutil
        for role in ('direct','indirect'):
            for variant in ('raw','processed'):
                shutil.copy2(self.maps[role],masters/(role+'_response_'+variant+'.exr'))
        before=preview_identity(self.material)
        for variant in ('raw','processed'):
            with native.preview_variant(self.material,self.directory,variant) as (clone,record):
                self.assertEqual(record['mode'],'response')
                self.assertIsNone(role_nodes(clone,'ao')[0].image)
                self.assertEqual(record['preserved_unassigned_optional_samplers'],[])
                self.assertEqual({row['role'] for row in record['bindings'].values()},{'direct','indirect'})
            self.assertEqual(preview_identity(self.material),before)

suite=unittest.defaultTestLoader.loadTestsFromTestCase(NativeOptionalAOTests)
result=unittest.TextTestRunner(verbosity=2).run(suite)
receipt={'tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'skipped':len(result.skipped),
    'failure_details':[{'test':str(t),'traceback':v} for t,v in result.failures],
    'error_details':[{'test':str(t),'traceback':v} for t,v in result.errors],
    'addon_source':str(args.addon_source),'helper_sha256':hashlib.sha256((args.addon_source/'workflow_tools/native_view_diagnostics.py').read_bytes()).hexdigest(),
    'addon_version':list(babylon_js.bl_info['version']),'addon_module_file':babylon_js.__file__,
    'operator_registered':bool(args.require_registered),'Blender':bpy.app.version_string,'rendered':False,'passed':result.wasSuccessful()}
(args.output/'result.json').write_text(json.dumps(receipt,indent=2))
if not result.wasSuccessful():raise SystemExit(1)
