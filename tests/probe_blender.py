"""Read-only compatibility probe. Run with Blender --background --python."""
from pathlib import Path
import sys, json, bpy
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
print('BLENDER_VERSION', bpy.app.version_string, sys.version, flush=True)
if bpy.data.filepath:
    from collections import Counter
    print('SCENE', bpy.data.filepath, 'ROLES', dict(Counter(o.get('role', 'none') for o in bpy.context.scene.objects)), flush=True)
    print('MATERIALS', json.dumps([{'name':m.name,'nodes':[n.bl_idname for n in m.node_tree.nodes], 'links':[(l.from_node.bl_idname,l.from_socket.name,l.to_node.bl_idname,l.to_socket.name) for l in m.node_tree.links]} for m in bpy.data.materials if m.users and m.use_nodes and 'shared' in m.name.lower()]),flush=True)
import babylon_js
babylon_js.register()
print('REGISTERED', flush=True)
from babylon_js.json_exporter import JsonExporter
out = Path(sys.argv[sys.argv.index('--')+1])
out.mkdir(parents=True, exist_ok=True)
exporter = JsonExporter()
exporter.execute(bpy.context, str(out / 'probe.babylon'), list(bpy.context.scene.objects))
print('EXPORT_RESULT', exporter.fatalError, exporter.nErrors, exporter.nWarnings, flush=True)
