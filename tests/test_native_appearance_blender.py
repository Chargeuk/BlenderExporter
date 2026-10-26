"""Native CPU fixture: attach once, leave physical albedo/source and illumination exact."""
import bpy,sys,json,hashlib
import numpy as np
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'));sys.path.insert(0,str(ROOT/'src/babylon_js/environment_tools'))
from babylon_js import colour_adjustment as ca
from babylon_js.lighting_controls import create_preview_material
from babylon_js.agx_lightmap import write_exr,combine_saved_controls,bake_albedo
from appearance import preview_identity
from ao import geometry_identity
from native_recombine import apply_finishing,restore_appearance
out=Path(sys.argv[sys.argv.index('--')+1]);out.mkdir(parents=True,exist_ok=False)
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def image(name,value):
 a=np.ones((16,16,4),np.float32);a[...,:3]=value;path=out/(name+'.exr');write_exr(path,a);im=bpy.data.images.load(str(path));im.colorspace_settings.name='sRGB' if name=='colour' else 'Non-Color';return im
preview,node=create_preview_material(image('colour',1),image('direct',2),image('indirect',1),image('ids',1));node.inputs['Shadow Lift'].default_value=0
bpy.ops.object.select_all(action='DESELECT');bpy.ops.mesh.primitive_plane_add();obj=bpy.context.object;obj.name='Appearance fixture receiver';obj.data.uv_layers.new(name='SimpleBake')
for d,uv in zip(obj.data.uv_layers[1].data,[(0,0),(1,0),(1,1),(0,1)]):d.uv=uv
mat=bpy.data.materials.new('Untinted physical');mat.use_nodes=True;obj.data.materials.append(mat);base=next(n for n in mat.node_tree.nodes if n.type=='BSDF_PRINCIPLED').inputs['Base Color'];base.default_value=(.4,.3,.2,1)
mapfile=out/'appearance.png';im=ca.create_image(mapfile,512);ca.tint_objects(im,[obj],(.8,1.2,1),1)
illumination=combine_saved_controls(bpy.context,16,out/'before.exr',material=preview);physical=preview_identity(mat);original=preview_identity(preview)
source=out/'physical.blend';bpy.ops.wm.save_as_mainfile(filepath=str(source));sourcehash=sha(source)
c={'_root':str(out),'_source':str(source),'preview_material':preview.name,'scene':bpy.context.scene.name,'uv':'SimpleBake','size':16,'receivers':{'names':[obj.name]},'contributors':{'names':[]}}
c['appearance_colour_adjustment']={'schema_version':1,'enabled':True,'image':str(mapfile),'sha256':sha(mapfile),'size':512,'colour_space':'sRGB','alpha_mode':'CHANNEL_PACKED','strength':1.0,'uv':'SimpleBake','uv_index':1,'geometry_sha256':geometry_identity(c)['geometry_sha256']}
first=apply_finishing(c);counts=(len(bpy.data.materials),len(bpy.data.node_groups));second=apply_finishing(c);assert counts==(len(bpy.data.materials),len(bpy.data.node_groups))
assert first['appearance_colour_adjustment']['applications']==second['appearance_colour_adjustment']['applications']==1
assert len([n for n in ca.controls(bpy.context.scene).node_tree.nodes if n.get('bjs_colour_adjustment')])==1
assert np.array_equal(illumination,combine_saved_controls(bpy.context,16,out/'after.exr',material=bpy.data.materials[c['preview_material']])),'Appearance leaked into illumination'
raw=bake_albedo(bpy.context,[obj],out/'raw-albedo.exr',16);config=ca.settings(bpy.context.scene);tinted=bake_albedo(bpy.context,[obj],out/'tinted-albedo.exr',16,colour_adjustment=config)
codes=ca.read_codes(config['image'])[256,256];factor=2*ca.srgb_decode(codes[:3]);assert np.max(abs(tinted[...,:3]-raw[...,:3]*factor))<1e-5,'Tint must apply once, not twice'
assert preview_identity(mat)==physical and preview_identity(preview)==original and sha(source)==sourcehash
restore_appearance(c);assert c['preview_material']==preview.name and ca.settings(bpy.context.scene) is None and preview_identity(preview)==original
(out/'result.json').write_text(json.dumps({'status':'passed','overlay_applications':1,'illumination_exact':True,'albedo_factor':factor.tolist(),'physical_source_graph_unchanged':True,'saved_source_hash_unchanged':True,'CPU':True},indent=2))
print('NATIVE_APPEARANCE_TEST_PASSED',flush=True)
