"""Run in factory background Blender with OpenImageIO and --addon/--output."""
import argparse
import json
from pathlib import Path
import sys
import unittest

import bpy
import numpy as np

parser = argparse.ArgumentParser()
parser.add_argument('--addon', required=True, type=Path)
parser.add_argument('--output', required=True, type=Path)
parser.add_argument('--full-capture', action='store_true')
args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
assert bpy.app.background
args.output.mkdir(parents=True, exist_ok=False)
sys.path[:0] = [str(args.addon.resolve().parent), str(args.addon.resolve() / 'environment_tools')]

from advanced_probe_capture import camera_rays, perspective_profile, perspective_renderer
from appearance import preview_identity
from babylon_js import bl_info, environment_controls
from babylon_js.environment_lighting_volume import (
    ADVANCED_PERSPECTIVE_NORMALIZATION, DIRECTION_ORDER, validate_advanced_provenance, validate_record,
)
from babylon_js.ktx_export import _write_png
from babylon_js.lighting_controls import create_preview_material
from common import save_json, sha
from probe_sources import prepared_sources, source_snapshot
from volume_capture import run
from worker import write_exr

assert tuple(bl_info['version']) == (3, 3, 62)


def make_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.engine = 'CYCLES'
    scene.cycles.device = 'CPU'
    scene.cycles.use_adaptive_sampling = scene.cycles.use_denoising = False
    scene.cycles.diffuse_bounces = 0
    scene.render.threads_mode = 'FIXED'
    scene.render.threads = 4
    scene.world = bpy.data.worlds.new('Fixture World')
    scene.world.color = (0, 0, 0)
    return scene


def wall(scene, name, axis, distance, material):
    axis = np.asarray(axis)
    tangent = np.cross(axis, [1, 0, 0] if axis[2] else [0, 0, 1])
    bitangent = np.cross(tangent, axis)
    vertices = [axis * distance + (tangent*x + bitangent*y) * 4
                for x, y in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(vertices, [], [(0, 1, 2, 3)])
    uv = mesh.uv_layers.new(name='SimpleBake')
    for loop, coords in zip(uv.data, ((0, 0), (1, 0), (1, 1), (0, 1))):
        loop.uv = coords
    mesh.materials.append(material)
    obj = bpy.data.objects.new(name, mesh)
    scene.collection.objects.link(obj)
    return obj


def material(name):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    tree = mat.node_tree
    tree.nodes.clear()
    out = tree.nodes.new('ShaderNodeOutputMaterial')
    return mat, tree, out.inputs['Surface']


def emission(name, color):
    mat, tree, surface = material(name)
    node = tree.nodes.new('ShaderNodeEmission')
    node.inputs['Color'].default_value = (*color, 1)
    tree.links.new(node.outputs[0], surface)
    return mat


def fixture_image(folder, name, color, width=8, height=8):
    path = folder / (name + '.exr')
    write_exr(path, np.broadcast_to(np.float32(color), (height, width, 4)).copy())
    image = bpy.data.images.load(str(path), check_existing=False)
    image.colorspace_settings.name = 'Non-Color'
    return image, path


class BlenderAdvancedProbeTests(unittest.TestCase):
    def test_mesh_only_explicit_receiver_emission_survives_baked_preparation(self):
        scene = make_scene()
        emit = emission('Authored receiver emitter', (.4, .3, .2))
        preview = emission('Managed baked display', (.2, .4, .6))
        preview.node_tree['bjs_preview_schema'] = 1
        regular = emission('Normal authored display', (.3, .3, .3))
        radiant = wall(scene, 'Radiant receiver', (1, 0, 0), 2, emit)
        display = wall(scene, 'Display receiver', (-1, 0, 0), 2, regular)
        layer = bpy.context.view_layer
        scope = dict(receivers=dict(names=[radiant.name, display.name]))
        roles = {radiant.name: {'0': 'emitter'}, display.name: {'0': 'baked-display'}}
        snapshot = source_snapshot(scene, layer, scope, roles)
        for obj in (radiant, display):
            obj.data.materials[0] = preview
        with prepared_sources(scene, layer, snapshot, dict(source_multiplier=4., material_roles=roles)) as diagnostics:
            self.assertEqual(diagnostics['receiverMode'], 'baked')
            self.assertEqual([row['object'] for row in diagnostics['boosted']], [radiant.name])
            updated = radiant.material_slots[0].material
            self.assertNotEqual(updated, emit)
            self.assertEqual(updated.node_tree.nodes['Emission'].inputs['Strength'].default_value, 4.)
            self.assertEqual(display.material_slots[0].material, preview)
            self.assertEqual(emit.node_tree.nodes['Emission'].inputs['Strength'].default_value, 1.)
        for obj in (radiant, display):
            self.assertEqual(obj.material_slots[0].link, 'DATA')
            self.assertEqual(obj.material_slots[0].material, preview)

    def test_native_cameras_and_axial_z(self):
        scene = make_scene()
        colors = [(1, .2, .4), (.2, 1, .4), (.4, .2, 1), (1, 1, .2), (1, .2, 1), (.2, 1, 1)]
        for axis, name, rgb in zip(
                ((1, 0, 0), (-1, 0, 0), (0, 0, 1), (0, 0, -1), (0, 1, 0), (0, -1, 0)),
                DIRECTION_ORDER, colors):
            wall(scene, name, axis, 2, emission(name, rgb))
        camera = bpy.data.objects.new('Fixture camera', bpy.data.cameras.new('Fixture camera'))
        scene.collection.objects.link(camera)
        scene.camera = camera
        scene.render.resolution_x = 320
        layer = bpy.context.view_layer
        layer.use_pass_combined = False
        world_override = bpy.data.worlds.new('Preview override')
        material_override = emission('Preview override', (0, 0, 0))
        layer.world_override = world_override
        layer.material_override = material_override
        profile = perspective_profile(scene, layer, dict(samples=4, fog_per_metre=.25, source_multiplier=1))
        env = np.ones((8, 16, 3))
        with perspective_renderer(scene, layer, profile, env, args.output) as (reference, render):
            measured = render([0, 0, 0])
        _, lengths = camera_rays(64, 70)
        expected = np.asarray(colors) * np.mean(np.exp(-.25 * 2 * lengths))
        np.testing.assert_allclose(reference, 1.)
        np.testing.assert_allclose(measured, expected, atol=.004)
        self.assertEqual(scene.render.resolution_x, 320)
        self.assertFalse(layer.use_pass_z)
        self.assertFalse(layer.use_pass_combined)
        self.assertEqual(layer.world_override, world_override)
        self.assertEqual(layer.material_override, material_override)
        self.assertEqual(camera.rotation_mode, 'XYZ')

    def test_two_height_pilot_with_real_lamp_and_shared_display(self):
        scene = make_scene()
        folder = args.output / 'fixture'
        folder.mkdir()
        diffuse, tree, surface = material('Authored floor')
        bsdf = tree.nodes.new('ShaderNodeBsdfDiffuse')
        bsdf.inputs['Color'].default_value = (.4, .4, .4, 1)
        tree.links.new(bsdf.outputs[0], surface)
        receiver = wall(scene, 'Diffuse floor', (0, 0, -1), 2, diffuse)
        luminaire, tree, surface = material('Shared strip and screen')
        group = bpy.data.node_groups.new('Shared emission group', 'ShaderNodeTree')
        group.interface.new_socket(name='Surface', in_out='OUTPUT', socket_type='NodeSocketShader')
        emit = group.nodes.new('ShaderNodeEmission')
        emit.inputs['Color'].default_value = (.4, .2, .1, 1)
        value = group.nodes.new('ShaderNodeValue')
        value.outputs[0].default_value = .8
        group.links.new(value.outputs[0], emit.inputs['Strength'])
        group.links.new(emit.outputs[0], group.nodes.new('NodeGroupOutput').inputs['Surface'])
        # The same group runs twice. Each emission branch is boosted once.
        add = tree.nodes.new('ShaderNodeAddShader')
        for socket in add.inputs:
            instance = tree.nodes.new('ShaderNodeGroup')
            instance.node_tree = group
            tree.links.new(instance.outputs[0], socket)
        tree.links.new(add.outputs[0], surface)
        strip = wall(scene, 'Fixture strip', (1, 0, 0), 2, luminaire)
        display = wall(scene, 'Fixture display', (-1, 0, 0), 2, luminaire)
        light = bpy.data.lights.new('Shared lamp data', 'POINT')
        light.energy = 60
        for name, x, hidden in (('Lamp A', -.3, False), ('Lamp B', .3, False), ('Hidden Lamp', 0., True)):
            obj = bpy.data.objects.new(name, light)
            obj.location = (x, 0, 0)
            obj.hide_render = hidden
            scene.collection.objects.link(obj)
        color_path = folder/'colour.png'
        _write_png(color_path, np.full((8, 8, 3), 102, np.uint8))
        color = bpy.data.images.load(str(color_path), check_existing=False)
        color.colorspace_settings.name = 'sRGB'
        preview, _ = create_preview_material(
            color,
            fixture_image(folder, 'direct', (1, 1, 1, 1))[0],
            fixture_image(folder, 'indirect', (.1, .1, .1, 1))[0],
            fixture_image(folder, 'ids', (1, 1, 1, 1))[0], scene=scene)
        reference, env_path = fixture_image(folder, 'env', (.15, .15, .15, 1), 16, 8)
        environment_controls.create_controls(scene.world, reference, connect=True)
        scene.world['bjs_environment_image'] = str(env_path)
        _, combined = fixture_image(folder, 'combined', (1.1, 1.1, 1.1, 1))
        proof = folder / 'combined.json'
        save_json(proof, dict(schema_version=1, combined_sha256=sha(combined), preview_sha256=preview_identity(preview)))
        scope = folder / 'scope.json'
        save_json(scope, dict(scene=scene.name, size=8, uv='SimpleBake', preview_material=preview.name,
                              receivers=dict(names=[receiver.name]), visible_emitters=dict(names=[strip.name, display.name]),
                              contributors=dict(names=[]), diffuse_bounces=0, total_bounces=2))
        source = folder / 'source.blend'
        bpy.ops.wm.save_as_mainfile(filepath=str(source))
        source_digest = sha(source)
        production = folder / 'production'
        config = dict(schema_version=2, source_scene=source.name, source_sha256=source_digest, scope_recipe=scope.name,
                      combined=combined.name, combined_provenance=proof.name, reference_environment=env_path.name,
                      output_dir=production.name, bounds=dict(min=[-.5, -.25, -.5], max=[.5, .5, .5]),
                      dimensions=[32, 16, 32], coarse_dimensions=[2, 2, 2], trial_indices=[[0, 0, 0]],
                      minimum_valid_probes=8, panorama=dict(size=32, samples=1, device='CPU'),
                      advanced=dict(samples=4, material_roles={display.name: {'0': 'baked-display'}}))
        results = []
        for boost in (1, 4):
            bpy.ops.wm.open_mainfile(filepath=str(source), load_ui=False)
            recipe = folder / f'recipe-{boost}.json'
            config['advanced']['source_multiplier'] = boost
            save_json(recipe, config)
            output = folder / f'pilot-{boost}'
            run(recipe, 'pilot', pilot_output=output, pilot_positions=[[0, 0, 0], [0, .2, 0]])
            results.append(json.loads((output/'advanced_pilot.json').read_text(encoding='utf8')))
            self.assertFalse(production.exists())
            self.assertEqual(sha(source), source_digest)
            self.assertEqual(bpy.data.objects['Lamp A'].data, bpy.data.objects['Lamp B'].data)
            self.assertEqual(bpy.data.objects['Lamp A'].data.energy, 60)
            self.assertTrue(bpy.data.objects['Hidden Lamp'].hide_render)
            self.assertEqual(bpy.data.objects['Fixture strip'].material_slots[0].material.name, 'Shared strip and screen')
        baseline, boosted = results
        diagnostics = boosted['sourceDiagnostics']
        self.assertEqual(diagnostics['receiverMode'], 'authored')
        self.assertEqual({row['object'] for row in diagnostics['boosted']}, {'Lamp A', 'Lamp B', 'Fixture strip'})
        self.assertIn('Hidden Lamp', {row['object'] for row in diagnostics['skipped']})
        self.assertIn('Fixture display', {row['object'] for row in diagnostics['skipped']})
        self.assertEqual([row['position'] for row in boosted['probes']], [[0, 0, 0], [0, .2, 0]])
        for before, after in zip(baseline['probes'], boosted['probes']):
            before, after = np.asarray(before['measuredRgb']), np.asarray(after['measuredRgb'])
            np.testing.assert_allclose(after[0], 4 * before[0], rtol=.025, atol=.0001)
            np.testing.assert_allclose(after[1], before[1], rtol=.025, atol=.0001)
            self.assertGreater(float(after[3].mean()), 1.3 * float(before[3].mean()))
        if args.full_capture:
            for phase in ('trial', 'capture'):
                bpy.ops.wm.open_mainfile(filepath=str(source), load_ui=False)
                run(recipe, phase)
            receipt = validate_record(json.loads((production/'volume_receipt.json').read_text()))
            advanced = receipt['advanced']
            self.assertEqual(advanced['normalization']['method'], ADVANCED_PERSPECTIVE_NORMALIZATION)
            provenance = json.loads((production/advanced['provenance']).read_text())
            validate_advanced_provenance(advanced, provenance, receipt['source'])
            self.assertEqual(len(provenance['checkpoints']), 8)
            self.assertEqual(sha(source), source_digest)


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(BlenderAdvancedProbeTests))
    save_json(args.output/'test-result.json', dict(tests=result.testsRun, failed=len(result.failures), errors=len(result.errors)))
    if not result.wasSuccessful():
        raise RuntimeError('Advanced native probe fixture failed')
