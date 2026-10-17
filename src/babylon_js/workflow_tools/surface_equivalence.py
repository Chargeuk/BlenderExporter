"""Exact convex-quad receiver tessellation evidence for offline delivery views."""
import hashlib
import json
from pathlib import Path
import numpy as np


def _array(value,shape):
    a=np.asarray(value,dtype=float)
    if a.shape!=shape or not np.isfinite(a).all():raise ValueError('Invalid finite surface array')
    return a


def _mesh_identity(obj,mesh,uv):
    h=hashlib.sha256();h.update(obj.name.encode())
    for values,dtype in [(obj.matrix_world,'<f4'),([v.co for v in mesh.vertices],'<f4'),
                          ([n.vector for n in mesh.corner_normals],'<f4'),
                          ([l.vertex_index for l in mesh.loops],'<i4'),
                          ([(p.loop_start,p.loop_total,p.use_smooth,p.material_index) for p in mesh.polygons],'<i4')]:
        h.update(np.asarray(values,dtype=dtype).tobytes())
    for name in ['UVMap',uv]:
        h.update(name.encode())
        layer=mesh.uv_layers.get(name)
        h.update(np.asarray([v.uv for v in layer.data] if layer else [],dtype='<f4').tobytes())
    return h.hexdigest()


def _identity_set(identities):
    return hashlib.sha256(json.dumps(identities,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def validate_surface_evidence(record, *, source_sha256, native_geometry_sha256):
    """Recompute evidence; boolean summaries never waive source/geometry checks."""
    from uv_audit import triangle_intersection_area
    if (record.get('schema_version')!=1 or record.get('kind')!='continuous-receiver-surface-equivalence'
            or record.get('source_sha256')!=source_sha256
            or record.get('native_geometry_sha256')!=native_geometry_sha256):
        raise ValueError('Surface equivalence source/native geometry differs')
    tolerance=2e-6
    native_names=record.get('native_mesh_names');runtime_names=record.get('runtime_mesh_names')
    for names in (native_names,runtime_names):
        if not isinstance(names,list) or not names or len(set(names))!=len(names):raise ValueError('Unique mesh selectors required')
    rows=record.get('faces')
    if not isinstance(rows,list) or not rows:raise ValueError('Explicit receiver/runtime face pairs required')
    seen_native=set();seen_runtime=set();native_tris=runtime_tris=extra=0
    replaced_native=set();replaced_runtime=set()
    for row in rows:
        nk=(row['receiver'],row['receiver_polygon']);rk=(row['runtime_object'],row['runtime_polygon'])
        if nk in seen_native or rk in seen_runtime:raise ValueError('Duplicate surface face pair')
        seen_native.add(nk);seen_runtime.add(rk);replaced_native.add(nk[0]);replaced_runtime.add(rk[0])
        if nk[0] not in native_names or rk[0] not in runtime_names:raise ValueError('Face outside exact selector sets')
        original=row['original_quad'];boundary=row['native_boundary']
        if len(original)!=4 or len(boundary)<4:raise ValueError('Convex original quad required')
        q=_array([x['world'] for x in original],(4,3))
        a=q[1]-q[0];b=q[3]-q[0];aa=np.dot(a,a);bb=np.dot(b,b)
        if min(aa,bb)<1e-12 or abs(np.dot(a,b))>tolerance*np.sqrt(aa*bb) or np.linalg.norm(q[0]+q[2]-q[1]-q[3])>tolerance:
            raise ValueError('Original face is not a nondegenerate planar rectangle')
        normal=np.cross(a,b);normal/=np.linalg.norm(normal)
        def project(points):
            points=np.asarray(points,dtype=float)-q[0]
            if np.max(np.abs(points@normal))>tolerance:raise ValueError('Tessellation left original plane')
            return np.stack((points@a/aa,points@b/bb),axis=-1)
        for field,width in [('normal',3),('UV1',2),('UV2',2)]:
            values=_array([x[field] for x in original],(4,width))
            if field=='normal':
                if np.max(np.linalg.norm(values-normal,axis=1))>tolerance:raise ValueError('Original normals/winding differ')
            elif np.linalg.norm(values[0]+values[2]-values[1]-values[3])>tolerance:
                raise ValueError('Original UV interpolation is not affine')
        boundary_points=_array([x['world'] for x in boundary],(len(boundary),3));project(boundary_points)
        matched=set();edge_positions=[]
        for value in boundary:
            point=_array(value['world'],(3,));found=False
            for i in range(4):
                edge=q[(i+1)%4]-q[i];t=np.dot(point-q[i],edge)/np.dot(edge,edge)
                if -1e-7<=t<=1+1e-7 and np.linalg.norm(point-q[i]-t*edge)<tolerance:
                    if t<1e-7:matched.add(i)
                    if t>1-1e-7:matched.add((i+1)%4)
                    for field in ('UV1','UV2','normal'):
                        first=np.asarray(original[i][field]);last=np.asarray(original[(i+1)%4][field]);wanted=first+t*(last-first)
                        if np.linalg.norm(np.asarray(value[field])-wanted)>tolerance:raise ValueError('Boundary world/UV/normal interpolation differs')
                    edge_positions.append(i+min(max(t,0),1));found=True
                    if 1e-7<t<1-1e-7:extra+=1
                    break
            if not found:raise ValueError('Native vertex outside original boundary')
        if matched!={0,1,2,3}:raise ValueError('Original corner missing from native boundary')
        differences=np.mod(np.diff(edge_positions+[edge_positions[0]]),4)
        if abs(float(differences.sum())-4)>tolerance or np.any(differences<1e-8):raise ValueError('Native boundary winding/order differs')
        areas=[]
        for key in ('native_triangles','runtime_triangles'):
            triangles=row[key]
            if not triangles:raise ValueError('Missing surface triangles')
            projected=[];total=0
            for triangle in triangles:
                vertices=_array(triangle['vertices'],(3,3));uv=project(vertices)
                if np.min(uv)<-tolerance or np.max(uv)>1+tolerance:raise ValueError('Triangle leaves original surface')
                permitted=boundary_points if key=='native_triangles' else q
                if any(np.min(np.linalg.norm(permitted-v,axis=1))>tolerance for v in vertices):raise ValueError('Triangle uses unauthored vertex')
                cross=np.cross(vertices[1]-vertices[0],vertices[2]-vertices[0]);area=np.linalg.norm(cross)*.5
                if area<1e-10 or np.dot(cross,normal)<=0:raise ValueError('Degenerate or reversed surface triangle')
                total+=area;projected.append(uv)
            for i,first in enumerate(projected):
                for second in projected[i+1:]:
                    if triangle_intersection_area(first,second)>1e-10:raise ValueError('Positive surface triangle overlap')
            if abs(total-np.sqrt(aa*bb))>tolerance:raise ValueError('Surface triangle coverage differs')
            areas.append(total)
        native_tris+=len(row['native_triangles']);runtime_tris+=len(row['runtime_triangles'])
    if set(native_names)-replaced_native!=set(runtime_names)-replaced_runtime:
        raise ValueError('Unpaired geometry selectors differ')
    counts=record.get('counts',{})
    identities=record.get('mesh_identities',{})
    for key,names in [('native',native_names),('runtime',runtime_names)]:
        mesh_counts=counts.get(key)
        if not isinstance(mesh_counts,dict) or set(mesh_counts)!=set(names) or any(type(v)!=int or v<1 for v in mesh_counts.values()):raise ValueError('Exact per-mesh triangle counts required')
        identity=identities.get(key)
        if not isinstance(identity,dict) or set(identity)!=set(names) or _identity_set(identity)!=record.get(key+'_render_geometry_sha256'):
            raise ValueError('Exact native/runtime geometry identities required')
    for name in set(native_names)-replaced_native:
        if counts['native'][name]!=counts['runtime'][name] or identities['native'][name]!=identities['runtime'][name]:raise ValueError('Unpaired mesh geometry/UV1/UV2 identity differs')
    if sum(counts['native'][n] for n in replaced_native)!=native_tris or sum(counts['runtime'][n] for n in replaced_runtime)!=runtime_tris:
        raise ValueError('Face proof does not cover every replacement surface')
    result={'validated':True,'kind':'continuous-receiver-surface-equivalence','source_sha256':source_sha256,
      'native_geometry_sha256':native_geometry_sha256,'native_mesh_count':len(native_names),'runtime_mesh_count':len(runtime_names),
      'native_render_geometry_sha256':record['native_render_geometry_sha256'],'runtime_render_geometry_sha256':record['runtime_render_geometry_sha256'],
      'native_triangle_count':sum(counts['native'].values()),'runtime_triangle_count':sum(counts['runtime'].values()),
      'paired_faces':len(rows),'inserted_boundary_vertices':extra,'native_paired_triangles':native_tris,'runtime_paired_triangles':runtime_tris}
    return result


def capture_blender(c, pairs, runtime_names):
    """Read exact saved source/evaluated meshes; no material, selection or scene edits."""
    import bpy
    from ao import geometry_identity
    from common import sha
    if not bpy.app.background or bpy.context.scene.name!=c['scene'] or Path(bpy.data.filepath).resolve()!=Path(c['_source']).resolve():
        raise ValueError('Exact disposable saved source/scene required')
    native_names=c['receivers']['names']+c.get('glass',{}).get('names',[])
    runtime_names=list(runtime_names);names=set(native_names+runtime_names);meshes={};evaluated=[]
    # geometry_identity creates/clears temporary evaluated meshes itself.
    # Compute it before retaining any to_mesh references for the face proof.
    native_geometry_sha256=geometry_identity(c)['geometry_sha256']
    deps=bpy.context.evaluated_depsgraph_get()
    try:
        for name in names:
            obj=bpy.context.scene.objects[name];ev=obj.evaluated_get(deps);mesh=ev.to_mesh(preserve_all_data_layers=True,depsgraph=deps);mesh.calc_loop_triangles()
            meshes[name]=(obj,mesh);evaluated.append(ev)
        def face(name,index):
            obj,mesh=meshes[name];polygon=mesh.polygons[index]
            normal_matrix=obj.matrix_world.to_3x3().inverted().transposed()
            boundary=[{'world':list(obj.matrix_world@mesh.vertices[mesh.loops[i].vertex_index].co),
              'normal':list((normal_matrix@mesh.corner_normals[i].vector).normalized()),
              'UV1':list(mesh.uv_layers['UVMap'].data[i].uv),'UV2':list(mesh.uv_layers[c.get('uv','SimpleBake')].data[i].uv)} for i in polygon.loop_indices]
            triangles=[{'vertices':[list(obj.matrix_world@mesh.vertices[i].co) for i in tri.vertices]} for tri in mesh.loop_triangles if tri.polygon_index==index]
            return boundary,triangles
        rows=[]
        for pair in pairs:
            row={k:pair[k] for k in ['receiver','receiver_polygon','runtime_object','runtime_polygon']}
            row['native_boundary'],row['native_triangles']=face(pair['receiver'],pair['receiver_polygon'])
            row['original_quad'],row['runtime_triangles']=face(pair['runtime_object'],pair['runtime_polygon']);rows.append(row)
        identities={'native':{n:_mesh_identity(*meshes[n],c.get('uv','SimpleBake')) for n in native_names},
                    'runtime':{n:_mesh_identity(*meshes[n],c.get('uv','SimpleBake')) for n in runtime_names}}
        record={'schema_version':1,'kind':'continuous-receiver-surface-equivalence','source_sha256':sha(c['_source']),
          'native_geometry_sha256':native_geometry_sha256,'native_mesh_names':native_names,'runtime_mesh_names':runtime_names,
          'mesh_identities':identities,'native_render_geometry_sha256':_identity_set(identities['native']),
          'runtime_render_geometry_sha256':_identity_set(identities['runtime']),
          'counts':{'native':{n:len(meshes[n][1].loop_triangles) for n in native_names},'runtime':{n:len(meshes[n][1].loop_triangles) for n in runtime_names}},'faces':rows}
        record['validation']=validate_surface_evidence(record,source_sha256=record['source_sha256'],native_geometry_sha256=record['native_geometry_sha256'])
        return record
    finally:
        for ev in evaluated:ev.to_mesh_clear()


def verify_blender_evidence(c, proof):
    """Bind both mesh sets and every proved face to the currently loaded source."""
    pairs=[{k:row[k] for k in ['receiver','receiver_polygon','runtime_object','runtime_polygon']} for row in proof['faces']]
    current=capture_blender(c,pairs,proof['runtime_mesh_names'])
    if current!=proof:raise ValueError('Current Blender native/runtime surface evidence differs')
    return current['validation']


def main():
    import argparse,sys,bpy
    from common import load_config,save_json
    p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);p.add_argument('--pairs',type=Path,required=True)
    p.add_argument('--runtime-names',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args(sys.argv[sys.argv.index('--')+1:]);c=load_config(a.config)
    if bpy.context.scene.name!=c['scene']:bpy.context.window.scene=bpy.data.scenes[c['scene']]
    if a.output.exists():raise ValueError('Preserve earlier evidence; use new file')
    record=capture_blender(c,json.loads(a.pairs.read_text()),json.loads(a.runtime_names.read_text()))
    save_json(a.output,record);print(json.dumps(record['validation']))


if __name__=='__main__':
    import sys
    sys.path.insert(0,str(Path(__file__).resolve().parent))
    sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'environment_tools'))
    main()
