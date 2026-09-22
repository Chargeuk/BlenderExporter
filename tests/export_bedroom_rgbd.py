"""Prepare a separate RGBD test package from the accepted PBR export snapshot."""
import bpy,json,sys,shutil,subprocess
from pathlib import Path
import numpy as np
import OpenImageIO as oiio
REPO=Path(__file__).resolve().parents[1];sys.path.insert(0,str(REPO/'src'))
import babylon_js
from babylon_js.json_exporter import JsonExporter
from babylon_js import ktx_conversion as core
babylon_js.register()
ROOT=Path(r'H:\code\kadshowWeb\content\environments\grandBedroomDayRealisticV3')
R=ROOT/'blender/appearanceCorrections'
O=R/'rgbdInvestigation095';O.mkdir(exist_ok=False)
OUT=ROOT/'rgbd-test';OUT.mkdir(exist_ok=False)
TOOL=r'H:\code\texture-tools\KTX-Software-4.4.2\bin\ktx.exe'
assert Path(bpy.data.filepath).name=='appearance_091b_export_ready_glass_alpha.blend'
sourcehash=core.sha(Path(bpy.data.filepath))
accepted=core.read_json(ROOT/'complete-export-check.json')
for name,info in accepted['output_files'].items():assert core.sha(ROOT/name)==info['sha256']
s=bpy.context.scene
visible=[o for o in s.objects if o.type=='MESH' and o.get('role')=='VISIBLE_DELIVERY_AND_BAKE_RECEIVER']
helpers=[o for name in ['physicsObjects','navMeshFloor'] for o in bpy.data.objects[name].children_recursive if o.type=='MESH']
chosen=set(visible+helpers)
for o in list(chosen):
    p=o.parent
    while p:chosen.add(p);p=p.parent
for name in ['hasenv','hasskyboxbasis']:
    obj=bpy.data.objects.get(name)
    if not obj:obj=bpy.data.objects.new(name,None);s.collection.objects.link(obj)
    chosen.add(obj)
assert len(visible)==644 and len(helpers)==29
assert all(o.active_material.name=='GrandBedroom shared opaque v053' for o in visible)
mat=visible[0].active_material;mat.transparencyMode='0';mat.backFaceCulling=True;mat.use_backface_culling=True
marker=next(o for o in chosen if o.name.startswith('lightmap_'))
lm=ROOT/'blender/textures/lightmaps/grandBedroom_lightmap_v089.exr'
marker['bjs_lightmap_image']=str(lm)
# Companions are already accepted; do not export a redundant stock World panorama.
s.world.use_nodes=False;s.world.usePBRMaterials=True;s.world.inlineTextures=False
s.world.positionsPrecision=s.world.normalsPrecision=s.world.UVsPrecision=6;s.camera=None
ex=JsonExporter();ex.execute(bpy.context,str(OUT/'grandBedroomDay.babylon'),sorted(chosen,key=lambda o:o.name),
    ktx_options=dict(executable=TOOL,flip_y=True,codec='basis-lz',threads=8,auto_lightmap=True,lightmap_encoding='rgbd-v1'),
    material_options={'metallic':.5,'roughness':.4})
assert not ex.fatalError and not ex.nErrors,ex.fatalError
data=core.read_json(OUT/'grandBedroomDay.babylon')
assert len(data['materials'])==1 and data['materials'][0]['transparencyMode']==0
assert not data['materials'][0]['useAlphaFromAlbedoTexture']
assert not any('glass' in m['name'].lower() for m in data['meshes'])
node=next(m for m in data['meshes'] if m['name'].startswith('lightmap_'))
assert node['metadata']['kadshowLightmapEncoding']=='rgbd-v1'
requested=''.join(node['name'].split('lightmap_')[1:])+'.ktx2'
assert (OUT/requested).is_file()
for name in ['cubemap.basis','environment.env','grandBedroomDay_thumbnail.jpg']:
    shutil.copy2(ROOT/name,OUT/name);assert core.sha(ROOT/name)==core.sha(OUT/name)
entry=next(t for t in ex.ktx_report['textures'] if t.get('encoding')=='rgbd-v1')
assert entry['channels']==4 and entry['color_space']=='linear' and entry['codec']=='uastc' and not entry['mipmaps']
proc=subprocess.run([TOOL,'extract','--transcode','rgba8','--raw',str(OUT/requested),str(O/'decoded_rgbd.rgba8')],capture_output=True,text=True)
assert proc.returncode==0,proc.stdout+proc.stderr
original=oiio.ImageBuf(str(lm)).get_pixels(oiio.FLOAT)[:,:,:3]
encoded=np.fromfile(O/'decoded_rgbd.rgba8',dtype=np.uint8).reshape(*original.shape[:2],4)[::-1].astype(np.float32)/255
minimum=node['metadata']['kadshowLightmapMinDivisor']
decoded=np.power(encoded[:,:,:3],2.2)/np.maximum(encoded[:,:,3:4],minimum)
assert np.isfinite(decoded).all()
labels=np.load(R/'maps/full084_indirect2048/island_labels.npy');owned=labels>0
error=np.abs(decoded-original);ldr_error=np.abs(np.clip(original,0,1)-original)
metrics=dict(original_max=float(original.max()),decoded_max=float(decoded.max()),alpha_min=float(encoded[:,:,3].min()),minimum_divisor=minimum,
    compressed_alpha_below_bound=int(np.sum(encoded[:,:,3]<minimum)),
    owned_mae=float(error[owned].mean()),owned_p99_abs_error=float(np.quantile(error[owned],.99)),
    owned_legacy_clipping_mae=float(ldr_error[owned].mean()),
    shadow_mae=float(error[owned & (original.max(2)<1)].mean()))
assert metrics['owned_mae']<metrics['owned_legacy_clipping_mae']*.25,metrics
old=core.read_json(ROOT/'grandBedroomDay.babylon');oldmesh={m['id']:m for m in old['meshes']}
for m in data['meshes']:
    if m['name'] in ['hasenv','hasskyboxbasis']:continue
    previous=oldmesh[m['id']]
    for key in ['positions','normals','uvs','uvs2','indices','parentId','position','rotation','scaling']:
        assert m.get(key)==previous.get(key),(m['name'],key)
triangles=sum(len(m.get('indices',[]))//3 for m in data['meshes'] if m['name'] in {o.name for o in visible})
assert triangles==19018
for name,info in accepted['output_files'].items():assert core.sha(ROOT/name)==info['sha256']
assert core.sha(Path(bpy.data.filepath))==sourcehash
report=dict(status='passed',output=str(OUT),source=bpy.data.filepath,source_hash=sourcehash,exporter=list(babylon_js.bl_info['version']),
    visible_triangles=triangles,helpers=len(helpers),glass=False,metadata=node['metadata']['kadshowLightmapEncoding'],
    lightmap=requested,metrics=metrics,staging=ex.ktx_report['staging'],legacy_package_unchanged=True,
    upload_files={name:{'bytes':(OUT/name).stat().st_size,'sha256':core.sha(OUT/name)} for name in ['grandBedroomDay.babylon','grandBedroomDay.babylon.manifest','grandBedroom_colour_v053.ktx2','grandBedroom_RM_v002.ktx2',requested,'cubemap.basis','environment.env','grandBedroomDay_thumbnail.jpg']})
(O/'export.json').write_text(json.dumps(report,indent=2));print('BEDROOM_RGBD_EXPORTED '+json.dumps(report),flush=True)
