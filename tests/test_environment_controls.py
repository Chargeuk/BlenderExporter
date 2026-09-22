"""Blender --background --factory-startup --python this.py -- OUT BASIS KTX.

Native World evaluation, source preservation, cleanup, and real staged UI export.
"""
import sys, json, math
from pathlib import Path
from unittest.mock import patch
import bpy
import numpy as np
import OpenImageIO as oiio
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import babylon_js
from babylon_js import environment_controls as ec, skybox_export as sky
from babylon_js import ktx_conversion as core
OUT, BASIS, KTX = sys.argv[sys.argv.index('--')+1:]
OUT=Path(OUT); OUT.mkdir(parents=True,exist_ok=True)
babylon_js.register()
world=bpy.context.scene.world; world.use_nodes=True
# Asymmetric HDR fixture exposes rotation/brightness mistakes.
pixels=np.zeros((128,256,3),np.float32)
pixels[:,:,0]=np.linspace(.05,6,256)[None,:]
pixels[:,:,1]=np.linspace(.1,2,128)[:,None]
pixels[:,:,2]=.3
path=OUT/'source.exr';spec=oiio.ImageSpec(256,128,3,oiio.FLOAT)
buf=oiio.ImageBuf(spec);buf.set_pixels(oiio.ROI.All,pixels);assert buf.write(str(path))
image=bpy.data.images.load(str(path));image.colorspace_settings.name='Linear Rec.709'
node=ec.create_controls(world,image,rotation=.35,lighting_strength=1.8,sky_lift=4,connect=True)
assert ec.create_controls(world,image,rotation=2,connect=True)==node
assert math.isclose(node.inputs['Rotation'].default_value,.35,abs_tol=1e-6)
before_scene=bpy.context.scene; before_world=before_scene.world; before_nodes=len(world.node_tree.nodes)
data_counts=[len(bpy.data.scenes),len(bpy.data.worlds),len(bpy.data.objects),len(bpy.data.cameras)]
original_hash=core.sha(path)
with ec.visible_panorama(bpy.context,OUT) as (p,values):
    first=oiio.ImageBuf(str(p)).get_pixels(oiio.FLOAT)
assert not p.exists() and not list(OUT.glob('world-sky-*'))
node.inputs['Lighting Strength'].default_value=30
node.inputs['Sky Lift'].default_value=80
with ec.visible_panorama(bpy.context,OUT) as (p,values):
    assert np.array_equal(first,oiio.ImageBuf(str(p)).get_pixels(oiio.FLOAT))
node.inputs['Visible Sky Strength'].default_value=2
with ec.visible_panorama(bpy.context,OUT) as (p,values):
    assert np.allclose(first*2,oiio.ImageBuf(str(p)).get_pixels(oiio.FLOAT),rtol=1e-6,atol=1e-6)
try:
    with ec.visible_panorama(bpy.context,OUT) as (p,values):
        raise RuntimeError('simulated consumer failure')
except RuntimeError as exc: assert 'consumer failure' in str(exc)
with patch.object(bpy.ops.render,'render',side_effect=RuntimeError('simulated render failure')):
    try:
        with ec.visible_panorama(bpy.context,OUT):pass
    except RuntimeError as exc: assert 'render failure' in str(exc)
assert not list(OUT.glob('world-sky-*'))
assert [len(bpy.data.scenes),len(bpy.data.worlds),len(bpy.data.objects),len(bpy.data.cameras)]==data_counts
assert bpy.context.scene==before_scene and before_scene.world==before_world
assert len(world.node_tree.nodes)==before_nodes and core.sha(path)==original_hash
assert Path(sky.world_panorama(bpy.context))==path.resolve()
# Real exporter operator with a managed World. A stale manual path is ignored.
world.usePBRMaterials=True;world.inlineTextures=False
result=bpy.ops.export.bjs(filepath=str(OUT/'delivery/fixture.babylon'),export_selected=True,
    export_skybox=True,basis_executable=BASIS,skybox_size='256',skybox_threads=2,
    skybox_image=str(OUT/'obsolete-copy.exr'))
assert result=={'FINISHED'}
report=core.read_json(OUT/'delivery/fixture.ktx-report.json')['skybox']
assert report['world_controls']['Visible Sky Strength']==2 and report['temporary_panorama_removed']
assert not list(OUT.rglob('visible.exr'))
assert not any('environmentTexture'==key for key in core.read_json(OUT/'delivery/fixture.babylon'))
# Explicit manual mode continues using its own image and rotation.
package=OUT/'manual';package.mkdir(exist_ok=True)
manual=sky.export_skybox(bpy.context,{},OUT/'manual-work',package,
    dict(executable=BASIS,image=str(path),size=256,threads=2,world_controls=False,rotation=180))
assert 'world_controls' not in manual
assert core.sha(path)==original_hash
babylon_js.unregister();babylon_js.register();babylon_js.unregister()
(OUT/'result.json').write_text(json.dumps({'status':'passed','checks':[
    'native node idempotence','visible branch independent of lighting gain','visible strength response',
    'temporary render cleanup on success and failures','context and datablock restoration',
    'real staged managed-World UI export','manual panorama compatibility','source hash unchanged',
    'no raw outdoor HDR exported as room environment','addon register cycle']},indent=2))
print('ENVIRONMENT_CONTROLS_TEST_PASSED',flush=True)
