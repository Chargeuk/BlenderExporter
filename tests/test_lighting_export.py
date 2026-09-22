"""Real Blender operator, direct and staged lighting-profile exports."""
import bpy, json, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
import babylon_js
from babylon_js.json_exporter import JsonExporter
from babylon_js.lighting_profile import DEFAULT_LIGHTING
babylon_js.register()
out=Path(sys.argv[sys.argv.index('--')+1]);out.mkdir(parents=True,exist_ok=False)
s=bpy.context.scene;s.world.usePBRMaterials=True;s.world.inlineTextures=False
marker=bpy.data.objects.new('lightmap_room',None);s.collection.objects.link(marker)
marker['keep']='unchanged';cube=s.objects['Cube'];cube.parent=marker
def check(name,options=None,staged=False):
    e=JsonExporter();e.execute(bpy.context,str(out/(name+'.babylon')),[cube,marker],
      lighting_options=options,ktx_options={'convert_materials':False} if staged else None)
    assert not e.fatalError and not e.nErrors,e.fatalError
    d=json.loads((out/(name+'.babylon')).read_text());m=next(n for n in d['meshes'] if n['name']=='lightmap_room')
    assert m['metadata']['keep']=='unchanged'
    expected={**DEFAULT_LIGHTING,**(options or {})}
    assert m['metadata']['kadshowLighting']==expected
    assert not any('kadshowLighting' in n.get('metadata',{}) for n in d['meshes'] if n['name']!='lightmap_room')
check('default');check('legacy',{'mode':'original'});check('staged',{'shadowSuppression':4,'fullyLitThreshold':2},True)
bpy.ops.object.select_all(action='DESELECT');cube.select_set(True);marker.select_set(True)
assert bpy.ops.export.bjs(filepath=str(out/'operator.babylon'),export_selected=True,
    lighting_shadow_suppression=3,lighting_fully_lit_threshold=1.5)=={'FINISHED'}
d=json.loads((out/'operator.babylon').read_text());m=next(n for n in d['meshes'] if n['name']=='lightmap_room')
assert m['metadata']['kadshowLighting']['shadowSuppression']==3
assert m['metadata']['kadshowLighting']['fullyLitThreshold']==1.5
target=out/'preserved.babylon';target.write_text('previous')
e=JsonExporter();e.execute(bpy.context,str(target),[cube,marker],lighting_options={'fullyLitThreshold':0})
assert e.fatalError and target.read_text()=='previous'
print('LIGHTING_EXPORT_TEST_PASSED',out)
