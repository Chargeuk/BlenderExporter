"""Disposable local diffuse reconstruction from prepared/decoded delivery channels."""
from contextlib import contextmanager
from pathlib import Path
import sys


def contract_for_views(c):
    sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'workflow_tools'))
    from decoded_delivery import load_contract
    from common import sha
    path=(Path(c['_root'])/c['capture']['views']['delivery_contract']).resolve()
    record=load_contract(path)
    if record['source_sha256']!=sha(c['_source']):raise ValueError('Delivery views require exact declared saved source')
    display=c['capture']['views'].get('display',{})
    if display!={'view_transform':'Raw','look':'None','exposure':0,'gamma':1}:
        raise ValueError('Delivery views require explicit Raw/None/exposure0/gamma1; shader applies gamma2.2 once')
    if record.get('continuous_surface_equivalence'):
        from surface_equivalence import verify_blender_evidence
        checked=verify_blender_evidence(c,record['continuous_surface_equivalence']['proof'])
        if checked!=record['continuous_surface_equivalence']['validation']:raise ValueError('Actual scene surface proof differs')
    record['contract_path']=str(path);record['contract_sha256']=sha(path)
    return record


def _overlay(tree,seen=None):
    seen=set() if seen is None else seen
    if tree.as_pointer() in seen:return False
    seen.add(tree.as_pointer())
    if tree.get('bjs_colour_adjustment_schema') or tree.get('bjs_physical_colour_adjustment'):return True
    return any(node.get('bjs_colour_adjustment') or node.get('bjs_physical_colour_adjustment') or
        (node.type=='GROUP' and node.node_tree and _overlay(node.node_tree,seen)) for node in tree.nodes)


@contextmanager
def delivery_materials(c,receivers,record,mode,stage):
    import bpy
    import numpy as np
    from babylon_js import colour_adjustment as ca
    from babylon_js.ktx_export import _write_png
    from appearance import preview_identity
    from common import sha
    if not bpy.app.background:raise RuntimeError('Delivery materials require disposable background Blender')
    variant='reference' if mode=='delivery-reference' else 'decoded'
    if mode not in ('delivery-reference','delivery-decoded'):raise ValueError('Invalid delivery view mode')
    images=[];groups=[];clones={};originals=[];used_atlases=set();source_materials={}
    def image_for(binding,role):
        array_path=Path(binding[variant+'_array']);array=np.load(array_path,allow_pickle=False)
        if array.dtype!=np.uint8 or array.ndim!=3 or array.shape[2]!=4:raise ValueError('Delivery image must be normalized top-left RGBA8')
        directory=stage/'delivery-images';directory.mkdir(exist_ok=True)
        path=directory/(array_path.stem+'.png')
        if path.exists():raise ValueError('Preserve existing delivery view image')
        _write_png(path,array)
        image=bpy.data.images.load(str(path),check_existing=False);images.append(image)
        image.colorspace_settings.name='Non-Color' if role=='rgbd' else 'sRGB'
        image.alpha_mode='CHANNEL_PACKED'
        return image
    try:
        lightmap=image_for(record['bindings']['lightmap'],'rgbd')
        tint=image_for(record['bindings']['tint'],'tint') if record['bindings'].get('tint') else None
        atlases={binding['image_name']:image_for(binding,'colour-atlas') for binding in record['bindings']['colour_atlases']}
        for obj in receivers:
            if obj.type!='MESH' or not obj.data.uv_layers.get(c.get('uv','SimpleBake')):raise ValueError('Delivery receiver requires exact UV2')
            materials=[slot.material for slot in obj.material_slots]
            if not materials or any(material is None for material in materials):raise ValueError('Delivery receiver lacks source material')
            for source in materials:
                if source in clones:continue
                if (not source.use_nodes or source.name==c.get('preview_material') or source.get('bjs_provisional_colour_tint')
                        or source.get('bjs_physical_colour_adjustment') or _overlay(source.node_tree)
                        or source.get('bjs_intrinsic_colour_role') not in (None,'untinted-source')):
                    raise ValueError('Delivery reconstruction requires untinted physical source material: '+source.name)
                principals=[node for node in source.node_tree.nodes if node.type=='BSDF_PRINCIPLED']
                if len(principals)!=1:raise ValueError('Delivery reconstruction requires one unambiguous Principled source: '+source.name)
                alpha=principals[0].inputs['Alpha']
                if alpha.is_linked or alpha.default_value!=1:raise ValueError('Delivery diffuse receivers must be opaque source materials')
                source_materials[source.name]=preview_identity(source)
                material=source.copy();clones[source]=material;tree=material.node_tree;nodes=tree.nodes;links=tree.links
                for node in nodes:
                    if node.type=='TEX_IMAGE' and node.image and node.image.name in atlases:
                        used_atlases.add(node.image.name);node.image=atlases[node.image.name]
                principal=next(node for node in nodes if node.type=='BSDF_PRINCIPLED');base=principal.inputs['Base Color']
                # Source Base Color precedes any temporary physical tint context. Reuse the existing overlay once.
                if tint:
                    group=ca.group(tint,c.get('uv','SimpleBake'));groups.append(group)
                    overlay=nodes.new('ShaderNodeGroup');overlay.node_tree=group
                    overlay.inputs['Strength'].default_value=record['model_contract']['tint_strength']
                    if base.is_linked:links.new(base.links[0].from_socket,overlay.inputs['Colour'])
                    else:overlay.inputs['Colour'].default_value=base.default_value
                    albedo=overlay.outputs[0]
                else:
                    value=nodes.new('ShaderNodeRGB');value.outputs[0].default_value=base.default_value
                    albedo=base.links[0].from_socket if base.is_linked else value.outputs[0]
                uv=nodes.new('ShaderNodeUVMap');uv.uv_map=c.get('uv','SimpleBake')
                texture=nodes.new('ShaderNodeTexImage');texture.image=lightmap;texture.extension='EXTEND';texture.interpolation='Linear'
                links.new(uv.outputs['UV'],texture.inputs['Vector'])
                gamma=nodes.new('ShaderNodeGamma');gamma.inputs['Gamma'].default_value=2.2;links.new(texture.outputs['Color'],gamma.inputs['Color'])
                bound=nodes.new('ShaderNodeMath');bound.operation='MAXIMUM';bound.inputs[1].default_value=record['model_contract']['minimum_divisor'];links.new(texture.outputs['Alpha'],bound.inputs[0])
                reciprocal=nodes.new('ShaderNodeMath');reciprocal.operation='DIVIDE';reciprocal.inputs[0].default_value=1;links.new(bound.outputs[0],reciprocal.inputs[1])
                illumination=nodes.new('ShaderNodeVectorMath');illumination.operation='SCALE';links.new(gamma.outputs[0],illumination.inputs[0]);links.new(reciprocal.outputs[0],illumination.inputs[3])
                product=nodes.new('ShaderNodeVectorMath');product.operation='MULTIPLY';links.new(albedo,product.inputs[0]);links.new(illumination.outputs[0],product.inputs[1])
                display=nodes.new('ShaderNodeGamma');display.inputs['Gamma'].default_value=1/2.2;links.new(product.outputs[0],display.inputs['Color'])
                emission=nodes.new('ShaderNodeEmission');links.new(display.outputs[0],emission.inputs['Color'])
                outputs=[node for node in nodes if node.type=='OUTPUT_MATERIAL' and node.is_active_output]
                if len(outputs)!=1:raise ValueError('Delivery material requires one active output')
                links.new(emission.outputs[0],outputs[0].inputs['Surface'])
            original=obj.data;slots=[(slot.link,slot.material) for slot in obj.material_slots];mesh=original.copy()
            originals.append((obj,original,mesh,slots));obj.data=mesh;mesh.materials.clear()
            for material in materials:mesh.materials.append(clones[material])
            for slot in obj.material_slots:slot.link='DATA'
        if set(atlases)!=used_atlases:raise ValueError('Colour-atlas bindings require direct source texture nodes; unused/nested atlas bindings rejected')
        yield {'kind':'local-decoded-delivery-diffuse','mode':mode,'variant':variant,'contract_sha256':record['contract_sha256'],
            'source_materials':source_materials,'minimum_divisor':record['model_contract']['minimum_divisor'],
            'tint_strength':record['model_contract']['tint_strength'],'colour_atlas_bindings':sorted(used_atlases),
            'rgbd_filter':'encoded UNORM bilinear then pow2.2 / max(filtered divisor, model minimum)',
            'tint_filter':'sRGB decode before linear interpolation; influence alpha applied once by existing colour group',
            'display':'shader pow1/2.2 then Raw/None exposure0 gamma1; no second AgX',
            'limits':record['limits']}
    finally:
        for obj,original,mesh,slots in reversed(originals):
            obj.data=original
            for slot,(link,material) in zip(obj.material_slots,slots):
                slot.link=link
                if link=='OBJECT':slot.material=material
            bpy.data.meshes.remove(mesh)
        for material in clones.values():bpy.data.materials.remove(material)
        for group in groups:
            if group.users==0:bpy.data.node_groups.remove(group)
        for image in images:bpy.data.images.remove(image)
