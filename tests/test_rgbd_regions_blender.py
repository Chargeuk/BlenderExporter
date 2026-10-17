"""Real Blender/KTX proof: independent colour codec, regions and runtime shadow intent.

blender -b --factory-startup --python test_rgbd_regions_blender.py -- NEW_OUT KTX_EXE
"""
import bpy
import json
from pathlib import Path
import sys
import numpy as np
import OpenImageIO as oiio

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
import babylon_js
from babylon_js import colour_adjustment as ca
from babylon_js import ktx_export as ktx
from babylon_js.json_exporter import JsonExporter

out=Path(sys.argv[sys.argv.index('--')+1]);out.mkdir(parents=True,exist_ok=False)
tool=sys.argv[sys.argv.index('--')+2];babylon_js.register()
require=lambda ok,message: None if ok else (_ for _ in ()).throw(AssertionError(message))
require(bpy.ops.export.bjs.get_rna_type().properties['colour_adjustment_codec'].default=='INHERIT','Codec default changed')
require(bpy.ops.export.bjs.get_rna_type().properties['ktx_lightmap_region_policy'].default=='','Region default changed')
scene=bpy.context.scene;scene.world.usePBRMaterials=True;scene.world.inlineTextures=False
cube=scene.objects['Cube'];cube.data.uv_layers.new(name='UV2')
array=np.full((32,32,4),255,np.uint8);array[...,:3]=[90,120,150];ktx._write_png(out/'atlas.png',array)
image=bpy.data.images.load(str(out/'atlas.png'))
material=bpy.data.materials.new('Region fixture material');material.use_nodes=True
principled=next(n for n in material.node_tree.nodes if n.type=='BSDF_PRINCIPLED')
texture=material.node_tree.nodes.new('ShaderNodeTexImage');texture.image=image
material.node_tree.links.new(texture.outputs['Color'],principled.inputs['Base Color'])
cube.data.materials.clear();cube.data.materials.append(material)
marker=bpy.data.objects.new('lightmap_fixture',None);scene.collection.objects.link(marker);cube.parent=marker
hdr=np.random.default_rng(17).uniform(.01,10,(32,32,3)).astype(np.float32)
image_spec=oiio.ImageSpec(32,32,3,oiio.FLOAT);buffer=oiio.ImageBuf(image_spec);buffer.set_pixels(oiio.ROI.All,hdr)
require(buffer.write(str(out/'lighting.exr')),'EXR write failed');marker['bjs_lightmap_image']=str(out/'lighting.exr')
labels=np.zeros((32,32),np.int32);labels[2:10,2:10]=1;labels[18:27,18:27]=2;labels[6:8,10:12]=3
np.save(out/'labels.npy',labels)
(out/'islands.json').write_text(json.dumps([dict(id=1,object='stepped'),dict(id=2,object='selected'),dict(id=3,object='protected')]))
policy=dict(schema_version=1,encoding='rgbd-v1',ownership_labels='labels.npy',islands='islands.json',selection=dict(ids=[1,2]),stepped_selection=dict(ids=[1]),guard_pixels=1)
(out/'regions.json').write_text(json.dumps(policy))
# Colour adjustment belongs to the managed preview; the export cube keeps its PBR material.
from babylon_js.lighting_controls import create_preview_material
image.colorspace_settings.name='sRGB'
direct=bpy.data.images.load(str(out/'lighting.exr'),check_existing=False);direct.colorspace_settings.name='Non-Color'
indirect=bpy.data.images.load(str(out/'lighting.exr'),check_existing=False);indirect.colorspace_settings.name='Non-Color'
ids=bpy.data.images.new('Fixture ownership preview',32,32,alpha=True,float_buffer=True);ids.colorspace_settings.name='Non-Color'
preview,_=create_preview_material(image,direct,indirect,ids,uv2='UV2',scene=scene)
ca.attach(scene,ca.create_image(out/'adjustment.png',512),uv='UV2')
sun=bpy.data.objects.new('Fixture runtime sun',bpy.data.lights.new('Fixture sun','SUN'));scene.collection.objects.link(sun)
sun['bjs_runtime_shadow_enabled']=False
results=[]
for codec in ('INHERIT','uastc'):
    options=dict(executable=tool,flip_y=True,codec='basis-lz',threads=2,lightmap_encoding='rgbd-v1',lightmap_agx=False,
        lightmap_region_policy=str(out/'regions.json'),colour_adjustment_enabled=True,colour_adjustment_codec=codec,colour_adjustment_size=512)
    exporter=JsonExporter();target=out/codec/'fixture.babylon'
    exporter.execute(bpy.context,str(target),[cube,marker,sun],ktx_options=options)
    require(not exporter.fatalError and not exporter.nErrors,str(exporter.fatalError))
    report=exporter.ktx_report;rows=report['textures']
    require(next(r for r in rows if r['output']=='atlas.ktx2')['codec']=='basis-lz','Global atlas codec changed')
    colour=next(r for r in rows if r['output']=='fixture_colour_adjustment.ktx2')
    require(colour['codec']==('basis-lz' if codec=='INHERIT' else 'uastc') and not colour['mipmaps'],'Independent colour codec failed')
    rgbd=next(r for r in rows if r.get('encoding')=='rgbd-v1')
    require(rgbd['rgbd_region']['outside_decoded_exact'] is True,'Outside decoded region differs')
    require(rgbd['rgbd_region']['mixed_blocks_retained_original']>0,'Mixed owned codec block was not protected')
    model=json.loads(target.read_text());require(next(l for l in model['lights'] if l['name']==sun.name)['shadowEnabled'] is False,'Runtime shadow policy omitted')
    results.append(dict(codec=codec,report=report))
(out/'RESULT.json').write_text(json.dumps(dict(status='passed',exports=results),indent=2))
print('RGBD_REGIONS_BLENDER_PASSED',flush=True)
