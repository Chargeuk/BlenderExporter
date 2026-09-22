"""Real Blender RGBD export, channel preservation and PBR opacity regression."""
import bpy,json,sys,subprocess
from pathlib import Path
import numpy as np
import OpenImageIO as oiio
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
import babylon_js
from babylon_js import ktx_export as ktx,ktx_conversion as core
from babylon_js.json_exporter import JsonExporter
OUT=Path(sys.argv[sys.argv.index('--')+1]);OUT.mkdir(parents=True,exist_ok=False)
TOOL=sys.argv[sys.argv.index('--')+2]
babylon_js.register()
assert bpy.ops.export.bjs.get_rna_type().properties['ktx_lightmap_encoding'].default=='legacy'
s=bpy.context.scene;s.world.usePBRMaterials=True;s.world.inlineTextures=False
cube=s.objects['Cube'];cube.data.uv_layers.new(name='UV2')
color=np.full((32,32,4),255,np.uint8);color[:,:,:3]=128;color[:16,:,3]=128;ktx._write_png(OUT/'albedo.png',color)
im=bpy.data.images.load(str(OUT/'albedo.png'))
material=bpy.data.materials.new('Opaque');material.use_nodes=True;material.transparencyMode='0'
p=next(n for n in material.node_tree.nodes if n.type=='BSDF_PRINCIPLED')
tex=material.node_tree.nodes.new('ShaderNodeTexImage');tex.image=im
material.node_tree.links.new(tex.outputs['Color'],p.inputs['Base Color'])
cube.data.materials.clear();cube.data.materials.append(material)
marker=bpy.data.objects.new('lightmap_source',None);s.collection.objects.link(marker);cube.parent=marker
hdr=np.empty((32,32,3),np.float32);hdr[:16]=[.001,.5,4];hdr[16:]=[1,8,63]
spec=oiio.ImageSpec(32,32,3,oiio.FLOAT);buf=oiio.ImageBuf(spec);buf.set_pixels(oiio.ROI.All,hdr);assert buf.write(str(OUT/'room_lightmap_v1.exr'))
marker['bjs_lightmap_image']=str(OUT/'room_lightmap_v1.exr')
material_glass=material.copy();material_glass.name='Glass';material_glass.transparencyMode='2'
pg=next(n for n in material_glass.node_tree.nodes if n.type=='BSDF_PRINCIPLED');pg.inputs['Base Color'].default_value=(1,1,1,.1)
for link in list(pg.inputs['Base Color'].links):material_glass.node_tree.links.remove(link)
material_cutout=material.copy();material_cutout.name='Cutout';material_cutout.transparencyMode='1'
objects=[cube,marker]
for mat in [material_glass,material_cutout]:
    obj=cube.copy();obj.data=cube.data.copy();obj.parent=None;obj.data.materials.clear();obj.data.materials.append(mat);s.collection.objects.link(obj);objects.append(obj)
options=dict(executable=TOOL,flip_y=True,codec='basis-lz',threads=2,lightmap_encoding='rgbd-v1')
exporter=JsonExporter();exporter.execute(bpy.context,str(OUT/'delivery/fixture.babylon'),objects,ktx_options=options)
assert not exporter.fatalError and not exporter.nErrors,exporter.fatalError
d=core.read_json(OUT/'delivery/fixture.babylon');mats={m['name']:m for m in d['materials']}
assert mats['Opaque']['transparencyMode']==0 and mats['Opaque']['alpha']==1
assert mats['Opaque']['useAlphaFromAlbedoTexture'] is False and mats['Opaque']['albedoTexture']['hasAlpha'] is False
assert mats['Glass']['transparencyMode']==2 and abs(mats['Glass']['alpha']-.1)<1e-6
assert mats['Cutout']['transparencyMode']==1 and mats['Cutout']['albedoTexture']['hasAlpha']
assert next(t for t in exporter.ktx_report['textures'] if t['output']=='albedo.ktx2')['channels']==4
node=next(m for m in d['meshes'] if m['name'].startswith('lightmap_'))
assert node['name']=='lightmap_room_v1_rgbd' and node['metadata']['kadshowLightmapEncoding']=='rgbd-v1'
entry=next(t for t in exporter.ktx_report['textures'] if t.get('encoding')=='rgbd-v1')
assert node['metadata']['kadshowLightmapMinDivisor']==entry['alpha_min']/255
assert entry['channels']==4 and entry['color_space']=='linear' and not entry['mipmaps'] and entry['alpha_min']>0
proc=subprocess.run([TOOL,'extract','--transcode','rgba8','--raw',str(OUT/'delivery'/entry['output']),str(OUT/'decoded.rgba8')],capture_output=True,text=True);assert proc.returncode==0,proc.stdout+proc.stderr
decoded=np.fromfile(OUT/'decoded.rgba8',dtype=np.uint8).reshape(32,32,4)[::-1].astype(np.float32)/255
linear=np.power(decoded[:,:,:3],2.2)/np.maximum(decoded[:,:,3:4],node['metadata']['kadshowLightmapMinDivisor'])
error=np.abs(linear-hdr)
assert linear.max()>60 and np.isfinite(linear).all()
assert float(error.mean())<.3,float(error.mean())
(OUT/'result.json').write_text(json.dumps(dict(status='passed',blender=bpy.app.version_string,encoding=entry,decode_mae=float(error.mean()),decode_max_error=float(error.max()),checks=['legacy default','opaque/glass/cutout intent','canonical marker and RGBD metadata','real UASTC RGBA UNORM','nonzero alpha divisor','HDR survives decode']),indent=2))
print('RGBD_BLENDER_TEST_PASSED',flush=True)
