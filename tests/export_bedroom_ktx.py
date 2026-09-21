"""Real 20K scene export through the integrated optional KTX workflow."""
import bpy
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
import babylon_js
from babylon_js.json_exporter import JsonExporter
from babylon_js.ktx_export import infer_lightmap
from babylon_js import ktx_conversion as core
babylon_js.register()
args=sys.argv[sys.argv.index('--')+1:]
out=Path(args[0]).resolve();out.mkdir(parents=True,exist_ok=False)
tool=args[1]
source=Path(bpy.data.filepath)
before=core.sha(source)
scene=bpy.context.scene
visible=[o for o in scene.objects if o.type=='MESH' and o.get('role')=='VISIBLE_DELIVERY_AND_BAKE_RECEIVER']
helpers=[o for name in ('physicsObjects','navMeshFloor') for o in bpy.data.objects[name].children_recursive if o.type=='MESH']
chosen=set(visible+helpers)
for obj in list(chosen):
    parent=obj.parent
    while parent:
        chosen.add(parent);parent=parent.parent
marker=next(o for o in chosen if o.name.startswith('lightmap_'))
old_marker=marker.name
lightmap=source.parent/'maps/mirror8_final.exr'
assert core.sha(lightmap)=='27c6f5afe513ac783f17f673f6947445af954534c699aa6c85fc7a3f9b657b31'
# Explicit source association is the strongest automatic default. In memory only;
# do not silently choose a newer historical bake by timestamp.
marker['bjs_lightmap_image']=str(lightmap)
detected=infer_lightmap(bpy.context,chosen)
assert detected['path']==str(lightmap.resolve())
scene.world.usePBRMaterials=True
scene.world.positionsPrecision=scene.world.normalsPrecision=scene.world.UVsPrecision=6
scene.world.inlineTextures=False;scene.camera=None
options=dict(executable=tool,flip_y=True,codec='basis-lz',threads=8,auto_lightmap=True)
exporter=JsonExporter();exporter.execute(bpy.context,str(out/'grandBedroomDay.babylon'),sorted(chosen,key=lambda o:o.name),ktx_options=options)
assert not exporter.fatalError,exporter.fatalError
assert exporter.nErrors==0
data=core.read_json(out/'grandBedroomDay.babylon')
raw=core.read_json(Path(exporter.ktx_report['staging'])/'source_export/grandBedroomDay.babylon')
assert len(visible)==633 and len(helpers)==29
visible_names={o.name for o in visible}
tris=sum(len(o.get('indices',[]))//3 for o in data['meshes'] if o['name'] in visible_names)
assert tris==20000
for before_node,after_node in zip(raw['meshes'],data['meshes']):
    restored=dict(after_node)
    if before_node['name']==old_marker:
        assert after_node['name']=='lightmap_mirror8_final'
        restored['name']=old_marker
    assert restored==before_node
assert marker.name==old_marker and core.sha(source)==before
for name in ('grandBedroom_colour_v053.ktx2','grandBedroom_RM_v002.ktx2','mirror8_final.ktx2'):
    assert (out/name).is_file()
assert len(exporter.ktx_report['textures'])==3
(out/'integration-result.json').write_text(json.dumps(dict(status='passed',source_unchanged=True,
    visible_triangles=tris,visible_meshes=len(visible),helpers=len(helpers),
    geometry_uvs_normals_hierarchy_unchanged=True,lightmap_detection=detected),indent=2),encoding='utf8')
print('BEDROOM_KTX_INTEGRATION_PASSED',str(out),flush=True)
