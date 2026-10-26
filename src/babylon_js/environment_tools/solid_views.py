"""Source-bound solid/culling and explicit face-omission diagnostics; never saves.

Run with background Blender: --python solid_views.py -- --config recipe.json
--output fresh-directory. Scene-specific viewing contracts and mesh/face lists
belong in the recipe. These images do not establish exporter sidedness or PBR.
"""
from pathlib import Path
import argparse
import hashlib
import json
import re
import sys


def validate_config(c):
    from camera_views import validate_camera_set
    allowed={'schema_version','source_filename','source_sha256','scene','delivery_names',
             'expected_delivery_meshes','expected_delivery_triangles','dimensions','views','notes','viewing_contract'}
    if not isinstance(c,dict) or set(c)-allowed or c.get('schema_version')!=1:
        raise ValueError('Expected solid diagnostic schema_version1 and known fields')
    name=c.get('source_filename','')
    if not isinstance(name,str) or not name.lower().endswith('.blend') or any(x in name for x in ('/','\\',':')):
        raise ValueError('source_filename must be a .blend basename')
    if not isinstance(c.get('source_sha256'),str) or not re.fullmatch('[0-9a-f]{64}',c['source_sha256']):
        raise ValueError('Expected exact lowercase source SHA256')
    names=c.get('delivery_names')
    if not isinstance(names,list) or not names or any(not isinstance(n,str) or not n for n in names) or len(set(names))!=len(names):
        raise ValueError('delivery_names must be explicit unique nonempty mesh names')
    for key in ('expected_delivery_meshes','expected_delivery_triangles'):
        if isinstance(c.get(key),bool) or not isinstance(c.get(key),int) or c[key]<1:
            raise ValueError('Explicit positive '+key+' required')
    if c['expected_delivery_meshes']!=len(names):raise ValueError('Expected mesh count differs from membership')
    views=c.get('views')
    if not isinstance(views,list) or not views:raise ValueError('Explicit diagnostic views required')
    camera_entries=[]
    for view in views:
        keys={'id','camera','matrix_world','lens','camera_properties','include_names','omit_faces','full_delivery','backface_culling','label'}
        if not isinstance(view,dict) or set(view)-keys:raise ValueError('Unknown diagnostic view field')
        selected=view.get('include_names',names)
        if not isinstance(selected,list) or not selected or any(not isinstance(n,str) or n not in names for n in selected) or len(set(selected))!=len(selected):
            raise ValueError('View membership must be a unique nonempty delivery subset')
        omitted=view.get('omit_faces',{})
        if not isinstance(omitted,dict) or any(n not in selected for n in omitted):raise ValueError('Face omission object absent from view')
        for face_ids in omitted.values():
            if not isinstance(face_ids,list) or any(isinstance(i,bool) or not isinstance(i,int) or i<0 for i in face_ids) or len(set(face_ids))!=len(face_ids):
                raise ValueError('Face IDs must be unique nonnegative evaluated polygon indices')
        for flag in ('full_delivery','backface_culling'):
            if type(view.get(flag,False)) is not bool:raise ValueError('Diagnostic flags must be boolean')
        if view.get('full_delivery') and (set(selected)!=set(names) or any(omitted.values())):
            raise ValueError('Full delivery view cannot omit objects or faces')
        if not isinstance(view.get('label',''),str):raise ValueError('View label must be text')
        entry={k:v for k,v in view.items() if k in ('id','camera','matrix_world','lens','camera_properties')}
        entry['mode']='solid';camera_entries.append(entry)
    # Reuse the established camera syntax/range guards without entering either
    # physical or baked material preparation. Existing callers keep their modes.
    validate_camera_set({'scene':c.get('scene'),'capture':{'views':{
        'dimensions':c.get('dimensions'),'samples':1,'camera_set':camera_entries}}},diagnostic=True)
    return c


def capture(c,output,config_path):
    import bpy
    import bmesh
    import numpy as np
    from mathutils import Matrix
    from camera_views import _camera_data_receipt
    from common import sha,save_json
    if not bpy.app.background:raise RuntimeError('Solid diagnostics require disposable background Blender')
    validate_config(c)
    # Bind the effective recipe to the exact bytes named by the receipt before any edits.
    config_bytes=Path(config_path).read_bytes()
    # Compare JSON types too: Python equality would treat True and 1 as identical.
    effective_json=json.dumps(c,sort_keys=True,allow_nan=False)
    recorded_json=json.dumps(json.loads(config_bytes),sort_keys=True,allow_nan=False)
    if effective_json!=recorded_json:raise ValueError('Effective solid diagnostic config differs from recipe file')
    config_hash=hashlib.sha256(config_bytes).hexdigest()
    source=Path(bpy.data.filepath).resolve();out=Path(output).resolve()
    if source.name!=c['source_filename'] or sha(source)!=c['source_sha256']:
        raise ValueError('Loaded source filename/hash differs from recipe')
    if bpy.context.mode!='OBJECT' or bpy.data.is_dirty:raise ValueError('Clean saved Object mode required')
    if any(bpy.app.is_job_running(k) for k in ('RENDER','OBJECT_BAKE')):raise ValueError('Source has an active render/bake job')
    scene=bpy.context.scene
    if scene.name!=c['scene']:raise ValueError('Exact saved scene differs')
    if out.exists():raise ValueError('Fresh output directory required')
    members=[]
    for name in c['delivery_names']:
        obj=scene.objects.get(name)
        if obj is None or obj.type!='MESH':raise ValueError('Unknown/nonmesh delivery member: '+name)
        members.append(obj)
    eligible=set()
    def walk(layer,blocked=False):
        blocked=blocked or layer.exclude or layer.collection.hide_render
        if blocked:return
        eligible.update(layer.collection.objects)
        for child in layer.children:walk(child,blocked)
    walk(bpy.context.view_layer.layer_collection)
    bad=[o.name for o in members if o not in eligible or o.hide_viewport]
    if bad:raise ValueError('Delivery member excluded from evaluated view layer: '+', '.join(bad))
    deps=bpy.context.evaluated_depsgraph_get()
    generated=[i for i in deps.object_instances if i.is_instance and i.parent and i.parent.original in members]
    if generated:raise ValueError('Generated delivery instances require explicit evaluated membership; unsupported here')
    originals={o.name:o for o in members};base={};records=[]
    for obj in members:
        mesh=bpy.data.meshes.new_from_object(obj.evaluated_get(deps),preserve_all_data_layers=True,depsgraph=deps)
        mesh.calc_loop_triangles();h=hashlib.sha256()
        h.update(np.asarray(obj.matrix_world,dtype='<f8').tobytes())
        h.update(np.asarray([v.co for v in mesh.vertices],dtype='<f8').tobytes())
        h.update(np.asarray([list(t.vertices) for t in mesh.loop_triangles],dtype='<i4').tobytes())
        base[obj.name]=mesh
        records.append({'name':obj.name,'evaluated_polygons':len(mesh.polygons),'evaluated_triangles':len(mesh.loop_triangles),
                        'evaluated_geometry_sha256':h.hexdigest(),'original_hide_render':obj.hide_render,
                        'materials':[m.name if m else None for m in mesh.materials]})
    total=sum(r['evaluated_triangles'] for r in records)
    if len(members)!=c['expected_delivery_meshes'] or total!=c['expected_delivery_triangles']:
        raise ValueError('Evaluated delivery count differs from recipe')
    for view in c['views']:
        for name,ids in view.get('omit_faces',{}).items():
            if any(i>=len(base[name].polygons) for i in ids):raise ValueError('Evaluated face ID out of range: '+name)
        name=view.get('camera')
        if name and (scene.objects.get(name) is None or scene.objects[name].type!='CAMERA'):
            raise ValueError('Unknown saved camera: '+name)
    out.mkdir(parents=True)
    # All edits below exist only in this disposable process. Source objects are
    # hidden, evaluated copies are explicitly displayed, and no blend is saved.
    for obj in scene.objects:obj.hide_render=True
    collection=bpy.data.collections.new('TEMP solid diagnostic geometry');scene.collection.children.link(collection)
    gray=bpy.data.materials.new('TEMP opaque diagnostic gray');gray.diffuse_color=(.62,.64,.67,1)
    world=bpy.data.worlds.new('TEMP solid diagnostic background');world.color=(.06,.06,.06);scene.world=world
    scene.render.engine='BLENDER_WORKBENCH';scene.render.resolution_x=c['dimensions'][0];scene.render.resolution_y=c['dimensions'][1];scene.render.resolution_percentage=100
    scene.render.image_settings.file_format='PNG';scene.render.image_settings.color_mode='RGB';scene.render.image_settings.color_depth='8'
    scene.render.film_transparent=False;scene.render.use_compositing=False;scene.use_nodes=False
    for layer in scene.view_layers:layer.material_override=None
    scene.view_settings.view_transform='Standard';scene.view_settings.look='None';scene.view_settings.exposure=0;scene.view_settings.gamma=1
    sh=scene.display.shading;sh.light='STUDIO';sh.color_type='SINGLE';sh.single_color=(.62,.64,.67)
    sh.show_shadows=True;sh.show_cavity=True;sh.cavity_type='BOTH';sh.show_specular_highlight=False;sh.show_object_outline=True;sh.background_type='WORLD'
    results=[]
    for view in c['views']:
        copies=[];displayed=[];omitted=[]
        for name in view.get('include_names',c['delivery_names']):
            mesh=base[name].copy();ids=view.get('omit_faces',{}).get(name,[])
            removed_triangles=sum(max(0,len(mesh.polygons[i].vertices)-2) for i in ids)
            if ids:
                bm=bmesh.new();bm.from_mesh(mesh);bm.faces.ensure_lookup_table()
                bmesh.ops.delete(bm,geom=[bm.faces[i] for i in ids],context='FACES_ONLY');bm.to_mesh(mesh);bm.free();mesh.update()
            mesh.materials.clear();mesh.materials.append(gray)
            obj=bpy.data.objects.new('TEMP solid '+name,mesh);collection.objects.link(obj);obj.matrix_world=originals[name].matrix_world.copy();obj.show_transparent=False;obj.color=(.62,.64,.67,1)
            mesh.calc_loop_triangles();displayed.append({'source_object':name,'triangles':len(mesh.loop_triangles)})
            if ids:omitted.append({'source_object':name,'evaluated_polygon_ids':ids,'triangle_count':removed_triangles})
            copies.append(obj)
        saved=scene.objects.get(view.get('camera',''));data=saved.data.copy() if saved else bpy.data.cameras.new('TEMP solid camera')
        cam=bpy.data.objects.new('TEMP solid camera',data);collection.objects.link(cam)
        cam.matrix_world=Matrix(view['matrix_world']) if 'matrix_world' in view else saved.matrix_world.copy()
        if 'lens' in view:data.lens=view['lens']
        for key,value in view.get('camera_properties',{}).items():
            if key=='dof_use':data.dof.use_dof=value
            elif key=='dof_fstop':data.dof.aperture_fstop=value
            else:setattr(data,key,value)
        scene.camera=cam;sh.show_backface_culling=view.get('backface_culling',False)
        path=out/(view['id']+'.png');scene.render.filepath=str(path);bpy.ops.render.render(write_still=True)
        import OpenImageIO as oiio
        pixels=oiio.ImageBuf(str(path)).get_pixels(oiio.FLOAT)
        if pixels is None or pixels.shape[:2]!=(c['dimensions'][1],c['dimensions'][0]) or not np.isfinite(pixels).all():raise ValueError('Invalid diagnostic PNG')
        results.append({'id':view['id'],'label':view.get('label',''),'file':path.name,'sha256':sha(path),'full_delivery':view.get('full_delivery',False),
          'backface_culling':sh.show_backface_culling,'matrix_world':[list(r) for r in cam.matrix_world],'camera_properties':_camera_data_receipt(data),
          'dimensions':c['dimensions'],'engine':scene.render.engine,'display':{'view_transform':'Standard','look':'None','exposure':0,'gamma':1},
          'displayed':displayed,'displayed_triangles':sum(r['triangles'] for r in displayed),'omitted_faces':omitted,
          'material_override':'All included geometry, including glass/emitters, shown opaque gray; no PBR/emission/glass-surface claim'})
        bpy.data.objects.remove(cam,do_unlink=True);bpy.data.cameras.remove(data)
        for obj in copies:
            mesh=obj.data;bpy.data.objects.remove(obj,do_unlink=True);bpy.data.meshes.remove(mesh)
    if sha(source)!=c['source_sha256'] or sha(Path(config_path))!=config_hash:raise RuntimeError('Immutable source/config changed')
    result={'kind':'Explicit solid diagnostic views; not PBR/exported-sidedness/visibility acceptance','source':str(source),'source_sha256':sha(source),
      'config_sha256':config_hash,'source_saved':False,'delivery_membership':records,'delivery_triangles':total,'views':results,
      'helper_sha256':sha(Path(__file__)),'camera_helper_sha256':sha(Path(__file__).with_name('camera_views.py')),
      'viewing_contract':c.get('viewing_contract'),'notes':c.get('notes'),
      'limits':['Face omissions are diagnostic openings only and are recorded by original evaluated polygon ID.','Opaque gray with culling off shows reverse sides; this does not establish a separate rear polygon.','No serialized export, merged-loader, lighting or concealment acceptance claim.']}
    save_json(out/'result.json',result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--config',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else None)
    path=Path(args.config).resolve();result=capture(json.loads(path.read_text(encoding='utf-8')),args.output,path)
    print(json.dumps({'status':'solid-diagnostics-complete','views':len(result['views']),'source_saved':False}))


if __name__=='__main__':
    # Blender --python does not add the script directory to sys.path. Match the
    # sibling camera helper's direct-file bootstrap before local helper imports.
    sys.path.insert(0,str(Path(__file__).resolve().parent))
    main()
