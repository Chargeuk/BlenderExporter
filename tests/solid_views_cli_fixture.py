"""Save a disposable CLI fixture without importing solid_views or changing paths."""
import argparse,hashlib,json,sys
from pathlib import Path
import bpy
from mathutils import Vector

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True)
    args=parser.parse_args(sys.argv[sys.argv.index('--')+1:])
    out=Path(args.output).resolve();assert bpy.app.background and not out.exists();out.mkdir(parents=True)
    bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
    bpy.ops.mesh.primitive_cube_add();bpy.context.object.name='CLI fixture box'
    bpy.ops.mesh.primitive_plane_add(size=4,location=(0,0,-1));bpy.context.object.name='CLI fixture floor'
    extra=bpy.data.collections.new('CLI excluded fixture');bpy.context.scene.collection.children.link(extra)
    mesh=bpy.data.objects['CLI fixture box'].data.copy();helper=bpy.data.objects.new('CLI omitted helper',mesh);extra.objects.link(helper);helper.location=(50,0,0)
    next(c for c in bpy.context.view_layer.layer_collection.children if c.collection==extra).exclude=True
    source=out/'fixture.blend';bpy.ops.wm.save_as_mainfile(filepath=str(source))
    sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    def camera(position):
        p=Vector(position);m=(-p).to_track_quat('-Z','Y').to_matrix().to_4x4();m.translation=p;return [list(r) for r in m]
    recipe=dict(schema_version=1,source_filename=source.name,source_sha256=sha(source),scene=bpy.context.scene.name,
        delivery_names=['CLI fixture box','CLI fixture floor'],expected_delivery_meshes=2,expected_delivery_triangles=14,dimensions=[320,240],
        views=[dict(id='cli_complete',matrix_world=camera((6,2,2)),lens=40,camera_properties={'type':'ORTHO','ortho_scale':6},full_delivery=True,backface_culling=False),
               dict(id='cli_omitted_cap',matrix_world=camera((3,2,4)),lens=40,camera_properties={'type':'ORTHO','ortho_scale':6},omit_faces={'CLI fixture box':[0]},backface_culling=True)])
    config=out/'recipe.json';config.write_text(json.dumps(recipe,indent=2),encoding='utf-8')
    (out/'fixture-receipt.json').write_text(json.dumps(dict(source=str(source),source_sha256=sha(source),config=str(config),config_sha256=sha(config),helper_imported=False,path_bootstrap_performed=False),indent=2),encoding='utf-8')
    print('SOLID_VIEWS_DIRECT_CLI_FIXTURE_SAVED')

if __name__=='__main__':main()
