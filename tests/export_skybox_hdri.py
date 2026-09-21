"""Optional real HDRI smoke export at the UI's default 1024 face size."""
import json
from pathlib import Path
import sys
import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
import babylon_js
from babylon_js import ktx_conversion as core
args=sys.argv[sys.argv.index('--')+1:]
out,source,basis=Path(args[0]).resolve(),Path(args[1]).resolve(),args[2]
out.mkdir(parents=True,exist_ok=False)
babylon_js.register()
before=core.sha(source)
scene=bpy.context.scene
scene.world.inlineTextures=False
scene.world.usePBRMaterials=True
scene.camera=None
bpy.ops.object.select_all(action='DESELECT')
scene.objects['Cube'].select_set(True)
result=bpy.ops.export.bjs(filepath=str(out/'hdri.babylon'),export_selected=True,
    export_skybox=True,basis_executable=basis,skybox_image=str(source))
assert result=={'FINISHED'}
report=core.read_json(out/'hdri.ktx-report.json')
assert report['skybox']['face_size']==1024 and report['skybox']['source_hdr']
assert report['skybox']['mip_levels']==1 and report['skybox']['status']=='passed'
assert core.sha(source)==before
assert not bpy.data.objects.get('hasskyboxbasis')
model=core.read_json(out/'hdri.babylon')
assert sum(len(m.get('indices',[]))//3 for m in model['meshes'])==12
assert len([m for m in model['meshes'] if m['name']=='hasskyboxbasis'])==1
(out/'result.json').write_text(json.dumps(dict(status='passed',default_face_size=1024,
    source_preserved=True,skybox=report['skybox']),indent=2))
print('HDRI_SKYBOX_PASSED',str(out),flush=True)
