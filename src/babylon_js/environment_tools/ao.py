"""Scene-space AO on the exact lightmap UVs, independent of receiver colour/metal."""
import hashlib
import json
import bpy
import numpy as np


def geometry_identity(c):
    from worker import select
    h=hashlib.sha256(); deps=bpy.context.evaluated_depsgraph_get()
    def array(values,dtype): h.update(np.asarray(values,dtype=dtype).tobytes())
    roles={key:select(c,key) for key in ('receivers','contributors')}
    for role,objects in roles.items():
        h.update(role.encode())
        for o in sorted(objects,key=lambda x:x.name):
            h.update(o.name.encode()); array(o.matrix_world,'<f4')
            ev=o.evaluated_get(deps); mesh=ev.to_mesh(preserve_all_data_layers=True,depsgraph=deps)
            try:
                array([v.co for v in mesh.vertices],'<f4')
                array([v.vector for v in mesh.corner_normals],'<f4')
                array([l.vertex_index for l in mesh.loops],'<i4')
                array([(p.loop_start,p.loop_total,p.use_smooth) for p in mesh.polygons],'<i4')
                if role=='receivers':
                    array([v.uv for v in mesh.uv_layers[c.get('uv','SimpleBake')].data],'<f4')
            finally: ev.to_mesh_clear()
    h.update(json.dumps({'uv':c.get('uv','SimpleBake'),'size':c['size']},sort_keys=True).encode())
    return {'schema_version':1,'geometry_sha256':h.hexdigest(),
            'receivers':[o.name for o in roles['receivers']], 'contributors':[o.name for o in roles['contributors']]}


def bake(c,root,stage):
    from worker import setup,select,evaluate_receivers,target_image,raw_pixels
    from common import save_json
    from reuse import validate_catalogue
    settings=c['ao']; scene=setup(c)
    receivers=select(c,'receivers');contributors=select(c,'contributors')
    for o in scene.objects:
        if o.type=='MESH':o.hide_render=o not in receivers+contributors
        if o.type=='LIGHT':o.hide_render=True
    copies,catalogue=evaluate_receivers(c)
    # The temporary receivers replace the originals, preventing coincident AO blockers.
    for o in receivers:o.hide_render=True
    bpy.ops.object.select_all(action='DESELECT')
    for o in copies:o.select_set(True);o.hide_set(False)
    bpy.context.view_layer.objects.active=copies[0];bpy.ops.object.join();proxy=bpy.context.object
    restore=proxy.matrix_world.to_3x3().transposed()
    wanted=[(restore@v.vector).normalized() for v in proxy.data.attributes['_environment_world_normal'].data]
    proxy.data.normals_split_custom_set(wanted)
    err=max(((a.vector-b).length for a,b in zip(proxy.data.corner_normals,wanted)),default=0)
    if err>.001:raise ValueError('AO proxy normals changed')
    source=root/('reuse' if (root/'reuse/islands.json').exists() else 'bake')/'islands.json'
    if source.exists():validate_catalogue(json.loads(source.read_text()),catalogue)
    mat=bpy.data.materials.new('TEMP scalar AO');mat.use_nodes=True;mat.node_tree.nodes.clear()
    nodes=mat.node_tree.nodes;links=mat.node_tree.links
    ao=nodes.new('ShaderNodeAmbientOcclusion');ao.only_local=False;ao.inside=False
    ao.samples=settings['node_samples'];ao.inputs['Distance'].default_value=settings['distance']
    emission=nodes.new('ShaderNodeEmission');output=nodes.new('ShaderNodeOutputMaterial')
    links.new(ao.outputs['AO'],emission.inputs['Color']);links.new(emission.outputs[0],output.inputs['Surface'])
    proxy.data.materials.clear();proxy.data.materials.append(mat)
    for p in proxy.data.polygons:p.material_index=0
    proxy.data.uv_layers.active_index=proxy.data.uv_layers.find(c.get('uv','SimpleBake'))
    image=target_image(c,[mat]);scene.cycles.samples=settings['samples']
    # Blender's EMIT clear fills unowned alpha with 1. The target already starts
    # at zero, so preserve it to retain the actual raster coverage.
    bpy.ops.object.bake(type='EMIT',margin=0,use_clear=False,use_selected_to_active=False,uv_layer=c.get('uv','SimpleBake'))
    pixels=raw_pixels(image);mask=pixels[:,:,3]>0
    if not mask.any() or not np.isfinite(pixels).all() or pixels[:,:,:3][mask].min()<-.00001 or pixels[:,:,:3][mask].max()>1.00001:
        raise ValueError('AO must have owned finite scalar values in [0,1]')
    np.save(stage/'ao_raw.npy',pixels);save_json(stage/'islands.json',catalogue)
    return {'settings':settings,'joined_normal_max_error':err,'owned_pixels':int(mask.sum()),
            'minimum':float(pixels[:,:,:3][mask].min()),'maximum':float(pixels[:,:,:3][mask].max()),
            'method':'Cycles AO shader scalar -> EMIT; zero margin; no radiance denoiser'}
