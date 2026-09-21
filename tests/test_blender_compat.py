"""Focused Blender 5 compatibility regression, independent of the bedroom."""
import bpy, sys, json, io, math
from pathlib import Path
from mathutils import Vector
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'src'))
import babylon_js
babylon_js.register()
from babylon_js.json_exporter import JsonExporter
from babylon_js.materials.nodes.principled import PrincipledBJSNode
from babylon_js.logging import Logger
out=Path(sys.argv[sys.argv.index('--')+1]).resolve();out.mkdir(parents=True,exist_ok=False)
# All current Principled aliases and the scalar-to-colour Sheen Tint change.
log=Logger(str(out/'material_probe.log'))
material=bpy.data.materials.new('Test material');material.use_nodes=True
principled=next(n for n in material.node_tree.nodes if n.type=='BSDF_PRINCIPLED')
for socket,value in [('Subsurface Weight',.1),('Coat Weight',.2),('Sheen Weight',.3),('Anisotropic',.1)]:
    principled.inputs[socket].default_value=value
principled.inputs['Sheen Tint'].default_value=(.2,.4,.6,1)
wrapper=PrincipledBJSNode(principled,'Surface',False)
assert abs(wrapper.subsurfaceTranslucencyIntensity-.1)<1e-6
assert abs(wrapper.clearCoatIntensity-.2)<1e-6
assert abs(wrapper.sheenIntensity-.3)<1e-6
assert max(abs(a-b) for a,b in zip(wrapper.sheenColor,(.2,.4,.6)))<1e-6
log.close()
bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
parent=bpy.data.objects.new('Parent "quoted"',None);bpy.context.collection.objects.link(parent)
parent['fraction']=.25;parent['text']='C:\\example\\path\n"quoted"'
mesh=bpy.data.meshes.new('Fixture')
# A quad and triangle share a sharp boundary, plus an n-gon and a modifier.
mesh.from_pydata([(0,0,0),(1,0,0),(1,1,0),(0,1,0),(0,0,1),
                 (2,0,0),(3,0,0),(3.5,.5,0),(3,1,0),(2,1,0)],[],[(0,1,2,3),(0,4,1),(5,6,7,8,9)])
o=bpy.data.objects.new('Fixture "mesh"',mesh);bpy.context.collection.objects.link(o);o.parent=parent
for poly in mesh.polygons:poly.use_smooth=True
for edge in mesh.edges:edge.use_edge_sharp=True
mesh.normals_split_custom_set([(0,0,1)]*len(mesh.loops))
for name in ['UVMap','Lightmap']:
    layer=mesh.uv_layers.new(name=name)
    for loop in mesh.loops:layer.data[loop.index].uv=(loop.index*.02,loop.index*.03)
modifier=o.modifiers.new('Evaluated offset','DISPLACE');modifier.strength=.05;modifier.mid_level=0
image=bpy.data.images.new('packed_data',width=4,height=4);image.colorspace_settings.name='Non-Color'
image.filepath_raw=str(out/'packed.png');image.file_format='PNG';image.save()
plain=bpy.data.materials.new('Packed RGB');plain.use_nodes=True
n=plain.node_tree.nodes;p=next(x for x in n if x.type=='BSDF_PRINCIPLED')
tex=n.new('ShaderNodeTexImage');tex.image=image
sep=n.new('ShaderNodeSeparateColor');sep.mode='RGB'
plain.node_tree.links.new(tex.outputs['Color'],sep.inputs['Color'])
plain.node_tree.links.new(sep.outputs['Green'],p.inputs['Roughness'])
plain.node_tree.links.new(sep.outputs['Blue'],p.inputs['Metallic'])
mesh.materials.append(plain)
s=bpy.context.scene;s.camera=None;s.world.usePBRMaterials=True
s.world.positionsPrecision=s.world.normalsPrecision=s.world.UVsPrecision=6
exporter=JsonExporter();exporter.execute(bpy.context,str(out/'fixture.babylon'),[parent,o])
assert not exporter.fatalError and exporter.nErrors==0
data=json.loads((out/'fixture.babylon').read_text(encoding='utf-8'))
node=next(x for x in data['meshes'] if x['name']==parent.name)
assert node['metadata']=={'fraction':.25,'text':'C:\\example\\path\n"quoted"'}
m=next(x for x in data['meshes'] if x['name']==o.name)
dg=bpy.context.evaluated_depsgraph_get();e=o.evaluated_get(dg);evaluated=e.to_mesh(preserve_all_data_layers=True,depsgraph=dg);evaluated.calc_loop_triangles()
expected=set()
for tri in evaluated.loop_triangles:
    for vi,li in zip(tri.vertices,tri.loops):
        pos=evaluated.vertices[vi].co;normal=evaluated.corner_normals[li].vector
        expected.add(tuple(round(x,6) for x in (*[pos.x,pos.z,pos.y],*[normal.x,normal.z,normal.y],*evaluated.uv_layers[0].data[li].uv,*evaluated.uv_layers[1].data[li].uv)))
assert len(m['indices'])==len(evaluated.loop_triangles)*3
for i in m['indices']:
    row=tuple(m['positions'][3*i:3*i+3]+m['normals'][3*i:3*i+3]+m['uvs'][2*i:2*i+2]+m['uvs2'][2*i:2*i+2])
    assert row in expected,row
assert 'tangents' not in m, 'N-gon path should preserve source geometry without manufacturing tangents'
e.to_mesh_clear()
pbr=data['materials'][0]
assert pbr['metallicTexture']['gammaSpace'] is False
assert pbr['useMetallnessFromMetallicTextureBlue'] and pbr['useRoughnessFromMetallicTextureGreen']
assert pbr['useRoughnessFromMetallicTextureAlpha'] is False
babylon_js.unregister();babylon_js.register();babylon_js.unregister()
(out/'result.json').write_text(json.dumps({'status':'passed','blender':bpy.app.version_string,
 'checks':['registration cycle','current Principled sockets and coloured sheen','RGB packed material and linear texture flag','n-gon/quad tessellation','evaluated modifier','custom corner normals','two UV layers','quoted names and metadata strings','fractional Empty metadata']},indent=2),encoding='utf-8')
print('COMPATIBILITY_TESTS_PASSED',flush=True)
