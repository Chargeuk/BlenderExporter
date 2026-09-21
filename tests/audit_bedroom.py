"""Compare exported vertex/UV/normal data with the untouched evaluated source."""
import bpy, json, sys, math
from pathlib import Path
from collections import Counter, defaultdict
from mathutils import Vector
from itertools import product
out = Path(sys.argv[sys.argv.index('--')+1])
data = json.loads((out/'grandBedroomDay.babylon').read_text(encoding='utf-8'))
manifest = json.loads((out/'manifest.json').read_text(encoding='utf-8'))
meshes = {m['name']:m for m in data['meshes']}
dg = bpy.context.evaluated_depsgraph_get()
errors=[]; maximum_normal_error=0; checked=0
def key(pos,uv,uv2):
    return tuple(round(float(x),6) for x in (*pos,*uv,*uv2))
for name in manifest['visible_names']:
    o=bpy.data.objects[name];e=o.evaluated_get(dg);me=e.to_mesh(preserve_all_data_layers=True,depsgraph=dg);me.calc_loop_triangles()
    m=meshes[name];expected=defaultdict(list)
    for tri in me.loop_triangles:
        for vi,li in zip(tri.vertices,tri.loops):
            pos=me.vertices[vi].co
            uv=me.uv_layers[0].data[li].uv;uv2=me.uv_layers[1].data[li].uv
            normal=me.corner_normals[li].vector
            expected[key((pos.x,pos.z,pos.y),uv,uv2)].append((normal.x,normal.z,normal.y))
    n=len(m['positions'])//3
    assert len(m['uvs'])==len(m['uvs2'])==n*2
    assert len(m['normals'])==n*3
    assert len(m['indices'])//3==len(me.loop_triangles)
    for i in m['indices']:
        k=key(m['positions'][3*i:3*i+3],m['uvs'][2*i:2*i+2],m['uvs2'][2*i:2*i+2])
        if k not in expected:
            errors.append({'object':name,'reason':'position/UV mismatch'});break
        nrm=m['normals'][3*i:3*i+3]
        error=min(max(abs(a-b) for a,b in zip(nrm,n)) for n in expected[k])
        maximum_normal_error=max(maximum_normal_error,error)
        if error>0.00001:
            errors.append({'object':name,'reason':'corner normal changed','error':error});break
    e.to_mesh_clear();checked+=1
world_bounds={}
for name in manifest['visible_names']+manifest['helper_names']:
    e=bpy.data.objects[name].evaluated_get(dg)
    me=e.to_mesh();me.calc_loop_triangles()
    # Unused source vertices are not emitted. Compare the referenced geometry,
    # using local AABB corners as Babylon BoundingBox does after transformation.
    used={v for tri in me.loop_triangles for v in tri.vertices}
    axes=[(min(me.vertices[v].co[i] for v in used),max(me.vertices[v].co[i] for v in used)) for i in range(3)]
    points=[e.matrix_world @ Vector(corner) for corner in product(*axes)]
    # Blender Z-up to the exporter's default Babylon Y-up coordinates.
    world_bounds[name]={'min':[min(p[i] for p in points) for i in (0,2,1)],
                        'max':[max(p[i] for p in points) for i in (0,2,1)]}
    e.to_mesh_clear()
report={'objects_checked':checked,'maximum_normal_component_error':maximum_normal_error,'errors':errors,'world_bounds':world_bounds}
(out/'source_comparison.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in report.items() if k!='world_bounds'}),flush=True)
if errors:raise RuntimeError('Export differs from the evaluated source')
