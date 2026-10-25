"""Blender --background --factory-startup --python this.py -- FRESH_OUTPUT.

Native saved World properties, real operator/script export, and rollback guards.
No capture, material conversion, KTX executable, or ENV converter is required.
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace
import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import babylon_js
from babylon_js.json_exporter import JsonExporter
from babylon_js import lighting_defaults as defaults

out = Path(sys.argv[sys.argv.index('--') + 1]).resolve()
out.mkdir(parents=True, exist_ok=False)
babylon_js.register()
world = bpy.context.scene.world
world.usePBRMaterials = True
world.inlineTextures = False
marker = bpy.data.objects.new('hasenv', None)
bpy.context.scene.collection.objects.link(marker)
marker['keep'] = 'unchanged'
bpy.ops.object.select_all(action='DESELECT')
marker.select_set(True)
bpy.context.view_layer.objects.active = marker
props = bpy.ops.export.bjs.get_rna_type().properties
assert props['environment_lighting_defaults_enabled'].default is False
assert props['environment_lighting_default_mode'].default == 'advanced'
assert props['environment_lighting_multiplier'].default == 1


def metadata(path):
    model = json.loads(path.read_text(encoding='utf8'))
    matches = [mesh for mesh in model['meshes'] if mesh['name'].lower() == 'hasenv']
    assert len(matches) == 1 and not matches[0].get('positions') and not matches[0].get('indices')
    assert matches[0]['metadata']['keep'] == 'unchanged'
    return matches[0]['metadata']


old = out / 'older.babylon'
assert bpy.ops.export.bjs(filepath=str(old), export_selected=True) == {'FINISHED'}
assert defaults.KEY not in metadata(old)

# Saved controls survive a genuine .blend save/reload, including a literal zero.
world.bjs_environment_lighting_defaults_enabled = True
world.bjs_environment_lighting_default_mode = 'simple'
world.bjs_environment_lighting_multiplier = 0
saved = out / 'saved-defaults.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(saved))
bpy.ops.wm.open_mainfile(filepath=str(saved))
world = bpy.context.scene.world
assert defaults.saved_options(world) == dict(version=1, mode='simple', multiplier=0)
dialog = SimpleNamespace()
defaults.prefill(dialog, bpy.context)
assert dialog.environment_lighting_defaults_enabled and dialog.environment_lighting_multiplier == 0

# EXEC_DEFAULT is the existing prepare-export route; it inherits opted-in saved controls.
inherited = out / 'saved-operator.babylon'
assert bpy.ops.export.bjs(filepath=str(inherited), export_selected=True) == {'FINISHED'}
assert metadata(inherited)[defaults.KEY] == dict(version=1, mode='simple', multiplier=0)

for mode, multiplier in (('off', 0), ('simple', 10), ('advanced', 1)):
    target = out / (mode + '.babylon')
    assert bpy.ops.export.bjs(filepath=str(target), export_selected=True,
        environment_lighting_defaults_enabled=True, environment_lighting_default_mode=mode,
        environment_lighting_multiplier=multiplier) == {'FINISHED'}
    assert metadata(target)[defaults.KEY] == dict(version=1, mode=mode, multiplier=multiplier)

disabled = out / 'disabled.babylon'
assert bpy.ops.export.bjs(filepath=str(disabled), export_selected=True,
    environment_lighting_defaults_enabled=False) == {'FINISHED'}
assert defaults.KEY not in metadata(disabled)
assert defaults.saved_options(world) == dict(version=1, mode='simple', multiplier=0)

scripted = out / 'scripted.babylon'
exporter = JsonExporter()
exporter.execute(bpy.context, str(scripted), list(bpy.context.selected_objects),
    ktx_options=dict(convert_materials=False, environment_lighting_defaults=dict(mode='advanced', multiplier=10)))
assert not exporter.fatalError and not exporter.nErrors, exporter.fatalError
assert metadata(scripted)[defaults.KEY] == dict(version=1, mode='advanced', multiplier=10)

previous = out / 'preserved.babylon'
previous.write_text('previous accepted model', encoding='utf8')
exporter = JsonExporter()
exporter.execute(bpy.context, str(previous), list(bpy.context.selected_objects),
    ktx_options=dict(convert_materials=False, environment_lighting_defaults=dict(mode='off', multiplier=float('nan'))))
assert exporter.fatalError and previous.read_text() == 'previous accepted model'

babylon_js.unregister()
assert not hasattr(bpy.types.World, 'bjs_environment_lighting_multiplier')
babylon_js.register()
assert hasattr(bpy.types.World, 'bjs_environment_lighting_multiplier')
babylon_js.unregister()
print('LIGHTING_DEFAULTS_BLENDER_TEST_PASSED', out, flush=True)
