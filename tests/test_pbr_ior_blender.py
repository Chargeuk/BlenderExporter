"""Real Blender node parser/material serializer regression; background, no render/bake."""
import argparse
import hashlib
import io
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

import bpy

parser = argparse.ArgumentParser()
parser.add_argument('--addon-source', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--require-registered', action='store_true')
args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
assert bpy.app.background and not args.output.exists()
args.output.mkdir(parents=True)
sys.path.insert(0, str(args.addon_source.parent))
import babylon_js
from babylon_js.logging import Logger
from babylon_js.materials.material import BJSMaterial
from babylon_js.materials.nodes.abstract import REFRACTION_TEX

assert Path(babylon_js.__file__).resolve().parent == args.addon_source.resolve()
assert tuple(babylon_js.bl_info['version']) == (3, 3, 58)
if args.require_registered:
    assert bpy.ops.export.bjs.get_rna_type().identifier
logger = Logger(str(args.output / 'exporter.log'))
fixtures = {}


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise AssertionError('Duplicate serialized JSON key: ' + key)
        result[key] = value
    return result


def material(name, ior=1.333, alpha=.86, sss=0, colour=(.055, .19, .145)):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    mat.transparencyMode = '2'
    node = next(n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED')
    node.inputs['Base Color'].default_value = (*colour, alpha)
    node.inputs['Alpha'].default_value = alpha
    node.inputs['IOR'].default_value = ior
    node.inputs['Roughness'].default_value = .14
    node.inputs['Metallic'].default_value = 0
    node.inputs['Subsurface Weight'].default_value = sss
    node.inputs['Transmission Weight'].default_value = .65
    return mat, node


def serialize(mat, pbr=True):
    exporter = SimpleNamespace(
        textureFullPathDir=str(args.output),
        settings=SimpleNamespace(usePBRMaterials=pbr, environmentIntensity=1),
        material_options={'metallic': 1, 'roughness': .8})
    wrapper = BJSMaterial(mat, exporter)
    stream = io.StringIO()
    wrapper.to_json_file(stream)
    payload = json.loads(stream.getvalue(), object_pairs_hook=unique_keys)
    fixtures[mat.name] = payload
    return wrapper, payload


class PBRIORTests(unittest.TestCase):
    def only_ior(self, payload, ior):
        self.assertEqual(payload['subSurface'], {'indexOfRefraction': ior})
        self.assertNotIn('indexOfRefraction', payload)
        self.assertNotIn('metallicF0Factor', payload)
        self.assertNotIn('refractionTexture', payload)

    def test_water_nondefault_without_SSS_preserves_alpha_and_PBR(self):
        mat, _ = material('water')
        wrapper, payload = serialize(mat)
        self.assertIsNone(wrapper.bjsNodeTree.subsurfaceTranslucencyIntensity)
        self.only_ior(payload, 1.333)
        self.assertEqual(payload['customType'], 'BABYLON.PBRMaterial')
        self.assertEqual(payload['albedo'], [.055, .19, .145])
        self.assertEqual(payload['alpha'], .86)
        self.assertEqual(payload['transparencyMode'], 2)
        self.assertEqual(payload['metallic'], 0)
        self.assertEqual(payload['roughness'], .112)

    def test_default_1_5_without_SSS_keeps_omitted_default(self):
        _, payload = serialize(material('default', ior=1.5, alpha=1)[0])
        self.assertNotIn('subSurface', payload)
        self.assertNotIn('indexOfRefraction', payload)

    def test_glass_nondefault_without_SSS_does_not_enable_refraction(self):
        _, payload = serialize(material('glass', ior=1.45, alpha=.12)[0])
        self.only_ior(payload, 1.45)
        self.assertEqual(payload['alpha'], .12)

    def test_existing_SSS_tint_flags_intensity_alpha_and_single_IOR(self):
        _, payload = serialize(material('sss', sss=.25, alpha=.7, colour=(.2, .4, .6))[0])
        self.assertEqual(payload['subSurface'], {
            'isTranslucencyEnabled': True, '_isTranslucencyEnabled': True,
            'translucencyIntensity': .25, 'tintColor': [.2, .4, .6],
            'indexOfRefraction': 1.333})
        self.assertEqual(payload['alpha'], .7)
        self.assertNotIn('indexOfRefraction', payload)

    def test_existing_SSS_default_IOR_remains_explicit(self):
        _, payload = serialize(material('sss_default', ior=1.5, sss=.2)[0])
        self.assertEqual(payload['subSurface']['indexOfRefraction'], 1.5)
        self.assertTrue(payload['subSurface']['isTranslucencyEnabled'])

    def test_Standard_keeps_legacy_reciprocal_IOR(self):
        _, payload = serialize(material('standard')[0], pbr=False)
        self.assertEqual(payload['customType'], 'BABYLON.StandardMaterial')
        self.assertNotIn('subSurface', payload)
        self.assertAlmostEqual(payload['indexOfRefraction'], 1 / 1.333, places=4)

    def test_no_nodes_PBR_keeps_no_IOR(self):
        mat = bpy.data.materials.new('no_nodes')
        _, payload = serialize(mat)
        self.assertNotIn('subSurface', payload)
        self.assertNotIn('indexOfRefraction', payload)

    def test_non_Principled_node_with_no_IOR_keeps_no_IOR(self):
        mat, _ = material('diffuse')
        tree = mat.node_tree
        tree.nodes.clear()
        output = tree.nodes.new('ShaderNodeOutputMaterial')
        diffuse = tree.nodes.new('ShaderNodeBsdfDiffuse')
        tree.links.new(diffuse.outputs[0], output.inputs['Surface'])
        wrapper, payload = serialize(mat)
        self.assertIsNone(wrapper.bjsNodeTree.indexOfRefraction)
        self.assertNotIn('subSurface', payload)

    def test_nondefault_IOR_preserves_existing_clearcoat_fields(self):
        mat,node=material('clearcoat',ior=1.4)
        node.inputs['Coat Weight'].default_value=.4
        node.inputs['Coat Roughness'].default_value=.2
        _,payload=serialize(mat)
        self.only_ior(payload,1.4)
        self.assertEqual(payload['clearCoat']['indexOfRefraction'],1.4)
        self.assertEqual(payload['clearCoat']['_indiceOfRefraction'],1.4)
        self.assertEqual(payload['clearCoat']['intensity'],.4)
        self.assertEqual(payload['clearCoat']['roughness'],.2)

    def test_linked_IOR_keeps_existing_texture_path_without_scalar_fallback(self):
        mat, node = material('linked_ior')
        image = bpy.data.images.new('ior_fixture', 4, 4)
        image.filepath_raw = str(args.output / 'linked_ior.png')
        image.file_format = 'PNG'
        image.save()
        texture = mat.node_tree.nodes.new('ShaderNodeTexImage')
        texture.image = image
        mat.node_tree.links.new(texture.outputs['Color'], node.inputs['IOR'])
        wrapper, payload = serialize(mat)
        self.assertIsNone(wrapper.bjsNodeTree.indexOfRefraction)
        self.assertIn(REFRACTION_TEX, wrapper.bjsNodeTree.bjsTextures)
        self.assertNotIn('subSurface', payload)
        self.assertNotIn('indexOfRefraction', payload)


result = unittest.TextTestRunner(verbosity=2).run(
    unittest.defaultTestLoader.loadTestsFromTestCase(PBRIORTests))
logger.close()
fixture_path = args.output / 'materials.babylon'
fixture_path.write_text(json.dumps({'materials': list(fixtures.values())}, indent=2))
receipt = {
    'passed': result.wasSuccessful(), 'tests': result.testsRun,
    'errors': len(result.errors), 'failures': len(result.failures),
    'failure_details':[{'test':str(t),'traceback':v} for t,v in result.failures],
    'error_details':[{'test':str(t),'traceback':v} for t,v in result.errors],
    'addon_source': str(args.addon_source.resolve()), 'addon_version': list(babylon_js.bl_info['version']),
    'serializer_sha256': hashlib.sha256((args.addon_source / 'materials/material.py').read_bytes()).hexdigest(),
    'fixture_sha256': hashlib.sha256(fixture_path.read_bytes()).hexdigest(),
    'Blender': bpy.app.version_string, 'operator_registered': args.require_registered,
    'rendered': False, 'baked': False,
    'scope': 'Real Blender node parser plus production material serializer. Linked IOR checks existing scalar suppression/channel discovery, not texture processing or new textured-IOR support.'}
(args.output / 'result.json').write_text(json.dumps(receipt, indent=2))
if not result.wasSuccessful():
    raise SystemExit(1)
