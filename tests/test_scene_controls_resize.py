import bpy,json,sys,numpy as np,OpenImageIO as oiio
from pathlib import Path
R=Path(__file__).resolve().parents[1];sys.path.insert(0,str(R/'src'));import babylon_js
from babylon_js.lighting_profile import scene_lighting_profile,prefill_lighting_options
from babylon_js.json_exporter import JsonExporter
from babylon_js.ktx_export import prepare_blender,make_config
from babylon_js.ktx_conversion import resize_lightmap
O=Path(sys.argv[sys.argv.index('--')+1]);O.mkdir(parents=True,exist_ok=False)
babylon_js.register();s=bpy.context.scene;s.world.usePBRMaterials=True;s.world.inlineTextures=False
assert bpy.ops.export.bjs.get_rna_type().properties['ktx_lightmap_size'].default=='0'
mat=bpy.data.materials.new('Saved controls');mat.use_nodes=True;n=mat.node_tree.nodes.new('ShaderNodeGroup');g=bpy.data.node_groups.new('Controls','ShaderNodeTree');n.node_tree=g;n['bjs_lighting_controls']=1
for name,value in [('Shadow Suppression',3.),('Fully Lit Threshold',1.5),('Highlight Preservation',.25)]:
 g.interface.new_socket(name=name,in_out='INPUT',socket_type='NodeSocketFloat');n.inputs[name].default_value=value
s['bjs_lighting_controls_material']=mat.name
profile=scene_lighting_profile(bpy.context,{'shadowSuppression':7});assert profile['shadowSuppression']==3 and profile['fullyLitThreshold']==1.5
cube=s.objects['Cube'];marker=bpy.data.objects.new('lightmap_fixture',None);s.collection.objects.link(marker);cube.parent=marker
from types import SimpleNamespace
operator=SimpleNamespace();prefill_lighting_options(operator,bpy.context)
assert operator.lighting_shadow_suppression==3
from unittest.mock import patch
from bpy_extras.io_utils import ExportHelper
with patch.object(ExportHelper,'invoke',return_value={'RUNNING_MODAL'}) as dialog:
 assert babylon_js.JsonMain.invoke(operator,bpy.context,None)=={'RUNNING_MODAL'}
 assert dialog.call_count==1
assert operator.lighting_shadow_suppression==3
operator.lighting_shadow_suppression=5
profile['shadowSuppression']=operator.lighting_shadow_suppression
e=JsonExporter();e.execute(bpy.context,str(O/'scene.babylon'),[cube,marker],ktx_options={'convert_materials':False},lighting_options=profile);assert not e.fatalError,e.fatalError
d=json.loads((O/'scene.babylon').read_text());assert next(m for m in d['meshes'] if m['name']==marker.name)['metadata']['kadshowLighting']==profile
bpy.ops.object.select_all(action='DESELECT');cube.select_set(True);marker.select_set(True)
assert bpy.ops.export.bjs(filepath=str(O/'operator_override.babylon'),export_selected=True,lighting_shadow_suppression=5,lighting_fully_lit_threshold=1.5,lighting_highlight_preservation=.25)=={'FINISHED'}
d=json.loads((O/'operator_override.babylon').read_text());assert next(m for m in d['meshes'] if m['name']==marker.name)['metadata']['kadshowLighting']==profile
assert n.inputs['Shadow Suppression'].default_value==3
# Invalid linked controls cannot silently export their unused socket default.
v=mat.node_tree.nodes.new('ShaderNodeValue');mat.node_tree.links.new(v.outputs[0],n.inputs['Shadow Suppression'])
try:scene_lighting_profile(bpy.context);raise AssertionError('accepted linked value')
except ValueError:pass
mat.node_tree.links.remove(n.inputs['Shadow Suppression'].links[0])
a=np.zeros((1024,1024,4),np.float32);a[::2,::2,:3]=64;a[...,3]=1
sp=oiio.ImageSpec(1024,1024,4,oiio.FLOAT);buf=oiio.ImageBuf(sp);buf.set_pixels(oiio.ROI.All,a);assert buf.write(str(O/'source.exr'))
spec=dict(source_path=str(O/'source.exr'),output='fixture.ktx2',encoding='rgbd-v1',size=512,color_space='linear',alpha='preserve',flip_y=True)
png,metrics=prepare_blender(spec,O);assert metrics['dimensions']==[512,512] and metrics['source_dimensions']==[1024,1024]
import struct,zlib
blob=png.read_bytes();pos=8;chunks=[]
while pos<len(blob):
 length=struct.unpack('>I',blob[pos:pos+4])[0]
 if blob[pos+4:pos+8]==b'IDAT':chunks.append(blob[pos+8:pos+8+length])
 pos+=length+12
p=np.frombuffer(zlib.decompress(b''.join(chunks)),np.uint8).reshape(512,2049)[:,1:].reshape(512,512,4).astype(np.float32)/255;decoded=p[...,:3]**2.2/p[...,3:4];assert np.max(abs(decoded-16))<.1
marker['bjs_lightmap_image']=str(O/'source.exr')
config=make_config(O/'scene.babylon',O,{'lightmap':str(O/'source.exr'),'lightmap_encoding':'rgbd-v1','lightmap_size':1024},bpy.context,[marker]);assert config['textures'][-1]['size']==1024
(O/'result.json').write_text(json.dumps({'status':'passed','profile':profile,'resize':metrics},indent=2));print('SCENE_CONTROLS_RESIZE_PASSED')
