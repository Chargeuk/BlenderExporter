"""Headless real-encoder regression for the optional Blender KTX export path."""
import bpy
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import babylon_js
from babylon_js import ktx_export as ktx
from babylon_js import ktx_conversion as core
from babylon_js.json_exporter import JsonExporter

OUT = Path(sys.argv[sys.argv.index('--') + 1]).resolve()
TOOL = sys.argv[sys.argv.index('--') + 2]
OUT.mkdir(parents=True, exist_ok=False)
babylon_js.register()
assert bpy.ops.export.bjs.get_rna_type().properties['convert_to_ktx2'].default is False
assert ktx.find_ktx(str(OUT/'missing-ktx'))[0] == ''
assert ktx.find_ktx(TOOL)[0]
with patch.dict(os.environ, PATH=str(Path(TOOL).parent)):
    assert ktx.find_ktx()[0]

# Distinct rows/channels and alpha ensure preparation cannot silently flip twice,
# change RGB codes, swap channels, or discard transparent pixels.
pixels = np.empty((32,32,4), dtype=np.uint8)
for y in range(32):
    for x in range(32):
        pixels[y,x] = (x*7, y*7, (x+y)*3, 127 if y<16 else 255)
ktx._write_png(OUT/'colour.png', pixels)
spec = dict(source_path=str(OUT/'colour.png'), output='colour.ktx2', color_space='srgb', alpha='preserve', flip_y=True)
prepared = OUT/'prepared';prepared.mkdir()
png, metrics = ktx.prepare_blender(spec, prepared)
check = bpy.data.images.load(str(png), check_existing=False)
check.colorspace_settings.name='Non-Color'
result = np.asarray(check.pixels[:]).reshape(32,32,4)[::-1]
assert np.array_equal(np.rint(result*255).astype(np.uint8), pixels[::-1])
bpy.data.images.remove(check)

# 16-bit PNG is_float does not mean scene-linear HDR. Keep the high byte,
# matching the standalone baseline, without applying another gamma curve.
import struct, zlib
p16=(np.arange(32*32*4,dtype=np.uint16).reshape(32,32,4)*13)
p16[:,:,3]=65535
def chunk(kind,data):
    return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data))
rows=b''.join(b'\0'+row.astype('>u2').tobytes() for row in p16)
(OUT/'sixteen.png').write_bytes(b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',32,32,16,6,0,0,0))+chunk(b'IDAT',zlib.compress(rows))+chunk(b'IEND',b''))
spec16=dict(source_path=str(OUT/'sixteen.png'),output='sixteen.ktx2',color_space='srgb',alpha='opaque',flip_y=True)
png16,metrics16=ktx.prepare_blender(spec16,prepared)
assert metrics16['png_16_to_8']=='high byte (baseline)'
check=bpy.data.images.load(str(png16),check_existing=False);check.colorspace_settings.name='Non-Color'
actual=np.rint(np.asarray(check.pixels[:]).reshape(32,32,4)[::-1,:,:3]*255).astype(np.uint8)
assert np.array_equal(actual,(p16[::-1,:,:3]>>8).astype(np.uint8))
bpy.data.images.remove(check)

scene=bpy.context.scene
cube=scene.objects.get('Cube')
image=bpy.data.images.load(str(OUT/'colour.png'),check_existing=False)
material=bpy.data.materials.new('Colour');material.use_nodes=True
p=next(n for n in material.node_tree.nodes if n.type=='BSDF_PRINCIPLED')
tex=material.node_tree.nodes.new('ShaderNodeTexImage');tex.image=image
material.node_tree.links.new(tex.outputs['Color'],p.inputs['Base Color'])
material.node_tree.links.new(tex.outputs['Alpha'],p.inputs['Alpha'])
cube.data.materials.clear();cube.data.materials.append(material)
cube.data.uv_layers.new(name='Lightmap')
marker=bpy.data.objects.new('lightmap_previous',None);scene.collection.objects.link(marker);cube.parent=marker
scene.world.usePBRMaterials=True
scene.world.textureDir='custom-original-directory'
scene.world.inlineTextures=False

lm=bpy.data.images.new('Probe HDR',width=32,height=32,float_buffer=True)
lm.colorspace_settings.name='Non-Color'
lm.pixels.foreach_set(np.tile([.18,.5,2.,1.],32*32).astype(np.float32))
lm.filepath_raw=str(OUT/'lighting.exr');lm.file_format='OPEN_EXR';lm.save()
spec=dict(source_path=str(OUT/'lighting.exr'),output='light.ktx2',color_space='srgb',alpha='discard',flip_y=False)
png, metrics=ktx.prepare_blender(spec,prepared)
assert metrics['linear_max']>1.9 and metrics['pixels_above_one_percent']==100

# Automatic defaults: explicit association, exact marker stem, linked UV2,
# unique keyword, and refusal to pick arbitrarily between historical bakes.
marker['bjs_lightmap_image']=str(OUT/'lighting.exr')
assert ktx.infer_lightmap(bpy.context,[cube,marker])['path']==str((OUT/'lighting.exr').resolve())
del marker['bjs_lightmap_image']
marker.name='lightmap_lighting'
assert ktx.infer_lightmap(bpy.context,[cube,marker])['path']==str((OUT/'lighting.exr').resolve())
marker.name='lightmap_previous'
uv=material.node_tree.nodes.new('ShaderNodeUVMap');uv.uv_map=cube.data.uv_layers[1].name
material.node_tree.links.new(uv.outputs['UV'],tex.inputs['Vector'])
assert ktx.infer_lightmap(bpy.context,[cube,marker])['path']==str((OUT/'colour.png').resolve())
material.node_tree.nodes.remove(uv)
old_name=image.name;image.name='SimpleBake only candidate'
assert ktx.infer_lightmap(bpy.context,[cube,marker])['path']==str((OUT/'colour.png').resolve())
old_lm_name=lm.name;lm.name='SimpleBake second candidate'
assert ktx.infer_lightmap(bpy.context,[cube,marker])['ambiguous']
image.name=old_name;lm.name=old_lm_name

objects=[cube,marker]
plain=JsonExporter();plain.execute(bpy.context,str(OUT/'plain.babylon'),objects)
assert not plain.fatalError and plain.nErrors==0
before=core.read_json(OUT/'plain.babylon')
target=OUT/'delivery/fixture.babylon'
options=dict(executable=TOOL,flip_y=True,codec='basis-lz',threads=2,lightmap=str(OUT/'lighting.exr'))
converted=JsonExporter();converted.execute(bpy.context,str(target),objects,ktx_options=options)
assert not converted.fatalError, converted.fatalError
assert scene.world.textureDir=='custom-original-directory'
assert marker.name=='lightmap_previous'
after=core.read_json(target)
mesh_before=next(m for m in before['meshes'] if m['name']=='Cube')
mesh_after=next(m for m in after['meshes'] if m['name']=='Cube')
assert mesh_before==mesh_after
assert next(m for m in after['meshes'] if m['id']==marker.name)['name']=='lightmap_lighting'
assert after['materials'][0]['albedoTexture']['name']=='colour.ktx2'
assert after['materials'][0]['albedoTexture']['gammaSpace'] is True
assert (target.parent/'colour.ktx2').is_file() and (target.parent/'lighting.ktx2').is_file()
assert not (target.parent/'source_textures/colour.png').exists()
baseline_hash=core.sha(target)

# Converter failure cannot damage an existing delivered model or change settings.
with patch.object(core,'run_conversion',side_effect=RuntimeError('fixture encoder failure')):
    failed=JsonExporter();failed.execute(bpy.context,str(target),objects,ktx_options=options)
    assert failed.nErrors and 'fixture encoder failure' in failed.fatalError
assert core.sha(target)==baseline_hash
assert scene.world.textureDir=='custom-original-directory'
scene.world.inlineTextures=True
failed=JsonExporter();failed.execute(bpy.context,str(target),objects,ktx_options=options)
assert 'Inline' in failed.fatalError
scene.world.inlineTextures=False

# The real operator exposes and executes the option (no file chooser required).
bpy.ops.object.select_all(action='DESELECT');cube.select_set(True);marker.select_set(True)
result=bpy.ops.export.bjs(filepath=str(OUT/'operator.babylon'),export_selected=True,
                          convert_to_ktx2=True,ktx_executable=TOOL,ktx_threads=2)
assert result=={'FINISHED'}
babylon_js.unregister();babylon_js.register();babylon_js.unregister()
(OUT/'result.json').write_text(json.dumps(dict(status='passed',blender=bpy.app.version_string,
    checks=['PATH/override discovery','default off','exact PNG RGB/alpha/flip','HDR PNG clipping',
            'real material and lightmap conversion','unchanged geometry and source marker',
            'restored settings','preserved delivery on failure','inline incompatibility',
            'real UI operator execution','registration cycle','automatic lightmap property/name/UV2/keyword and ambiguity']),indent=2))
print('KTX_BLENDER_TEST_PASSED',str(OUT),flush=True)
