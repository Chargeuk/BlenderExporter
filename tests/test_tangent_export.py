import bpy,json,sys
from pathlib import Path
R=Path(__file__).resolve().parents[1];sys.path.insert(0,str(R/'src'))
import babylon_js
from babylon_js.json_exporter import JsonExporter
babylon_js.register()
O=Path(sys.argv[sys.argv.index('--')+1]) if '--' in sys.argv else R/'artifacts/tangents-3.3.11'
O.mkdir(parents=True,exist_ok=False)
s=bpy.context.scene;o=s.objects['Cube'];m=o.data
m.normals_split_custom_set([(0.1,0.1,1)]*len(m.loops))
assert m.has_custom_normals and len(m.uv_layers)
uv=m.uv_layers.new(name='Lightmap')
for a,b in zip(uv.data,m.uv_layers[0].data):a.uv=b.uv
assert s.world.exportTangents is True
def run(name,enabled,staged=False):
 s.world.exportTangents=enabled;e=JsonExporter();e.execute(bpy.context,str(O/(name+'.babylon')),[o],ktx_options={'convert_materials':False} if staged else None)
 assert not e.fatalError,e.fatalError
 return next(x for x in json.loads((O/(name+'.babylon')).read_text())['meshes'] if x.get('indices'))
a=run('on',True);b=run('off',False);c=run('staged_off',False,True)
assert a.get('tangents') and not b.get('tangents') and not c.get('tangents')
def expanded(x,key,n):return [x[key][i*n:i*n+n] for i in x['indices']]
for key,n in [('positions',3),('normals',3),('uvs',2),('uvs2',2)]:
 assert expanded(a,key,n)==expanded(b,key,n)==expanded(c,key,n),key
bpy.ops.wm.save_as_mainfile(filepath=str(O/'setting.blend'))
bpy.ops.wm.open_mainfile(filepath=str(O/'setting.blend'))
assert bpy.context.scene.world.exportTangents is False
(O/'result.json').write_text(json.dumps({'status':'passed','checks':['generic default enabled','disabled omits tangents','staged export omits tangents','triangle corner positions normals and both UV channels unchanged','setting persists through file reload']},indent=2))
print('TANGENT_REGRESSION_PASSED')
