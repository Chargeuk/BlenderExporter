"""Disposable local diffuse reconstruction from prepared/decoded delivery channels."""
from contextlib import contextmanager
from pathlib import Path
import sys
import re


def delivery_channel_plan(spec,record=None):
    """Opt-in diagnostic selection; never rewrite the validated delivery contract."""
    plan={'delivery-reference':{'colour_atlases':'reference','lightmap':'reference','tint':'reference'},
          'delivery-decoded':{'colour_atlases':'decoded','lightmap':'decoded','tint':'decoded'}}
    if 'delivery_channel_isolation' not in spec:return plan
    policy=spec['delivery_channel_isolation']
    if (not spec.get('delivery_contract') or not isinstance(policy,dict)
            or set(policy)!={'schema_version','variants'} or type(policy['schema_version']) is not int
            or policy['schema_version']!=1 or not isinstance(policy['variants'],dict)
            or not 1<=len(policy['variants'])<=8):
        raise ValueError('delivery_channel_isolation requires delivery_contract and schema1 named variants')
    names=set()
    for name,selection in policy['variants'].items():
        if (not isinstance(name,str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,39}',name)
                or name.casefold() in names):raise ValueError('Invalid delivery isolation variant name')
        names.add(name.casefold())
        if (not isinstance(selection,dict) or set(selection)-{'colour_atlases','lightmap','tint'}
                or not {'colour_atlases','lightmap'}<=set(selection)
                or any(value not in ('reference','decoded') for value in selection.values())):
            raise ValueError('Delivery isolation variants require reference/decoded channel selections')
        if record is not None:
            if record['bindings'].get('tint') and 'tint' not in selection:
                raise ValueError('Delivery isolation must explicitly select the existing tint')
            if not record['bindings'].get('tint') and 'tint' in selection:
                raise ValueError('Delivery isolation cannot enable absent tint')
        plan['delivery-isolation-'+name]=dict(selection)
    return plan


def selected_channel_bindings(record,selection):
    """Receipt of actual immutable generated arrays, not retagged contract copies."""
    from common import sha
    result=[]
    groups=[('lightmap',[record['bindings']['lightmap']]),
            ('colour_atlases',record['bindings']['colour_atlases'])]
    if record['bindings'].get('tint'):groups.append(('tint',[record['bindings']['tint']]))
    for channel,bindings in groups:
        variant=selection[channel]
        for binding in bindings:
            path=Path(binding[variant+'_array'])
            digest=sha(path)
            if record['generated'].get(str(path))!=digest:
                raise ValueError('Selected delivery array is not unchanged hash-validated generated data: '+str(path))
            row={'channel':channel,'variant':variant,'array':str(path),'array_sha256':digest,
                 'prepared':binding['prepared'],'prepared_sha256':binding['prepared_sha256'],
                 'ktx':binding['ktx'],'ktx_sha256':binding['sha256']}
            if 'image_name' in binding:row['image_name']=binding['image_name']
            result.append(row)
    return result


def contract_for_views(c):
    sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'workflow_tools'))
    from decoded_delivery import load_contract
    from common import sha
    path=(Path(c['_root'])/c['capture']['views']['delivery_contract']).resolve()
    record=load_contract(path)
    delivery_channel_plan(c['capture']['views'],record)
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
    spec=c['capture']['views'];diagnostic='delivery_channel_isolation' in spec
    plan=delivery_channel_plan(spec,record)
    if mode not in plan:raise ValueError('Invalid delivery view mode')
    selection=plan[mode]
    variant='reference' if mode=='delivery-reference' else 'decoded'
    if mode.startswith('delivery-isolation-'):variant='diagnostic'
    selected=selected_channel_bindings(record,selection) if diagnostic else None
    image_receipts=[]
    images=[];groups=[];clones={};originals=[];used_atlases=set();source_materials={}
    def image_for(binding,role):
        channel={'rgbd':'lightmap','tint':'tint','colour-atlas':'colour_atlases'}[role]
        chosen=selection[channel]
        array_path=Path(binding[chosen+'_array']);array=np.load(array_path,allow_pickle=False)
        if array.dtype!=np.uint8 or array.ndim!=3 or array.shape[2]!=4:raise ValueError('Delivery image must be normalized top-left RGBA8')
        directory=stage/'delivery-images'
        if diagnostic:
            directory=directory/mode/channel;directory.mkdir(parents=True,exist_ok=True)
            # Long source stems plus diagnostic directories can exceed Windows MAX_PATH.
            # The receipt retains the complete source path and hashes; use a short local index.
            path=directory/(str(len(image_receipts)).zfill(3)+'.png')
        else:
            directory.mkdir(exist_ok=True)
            path=directory/(array_path.stem+'.png')
        if path.exists():raise ValueError('Preserve existing delivery view image')
        _write_png(path,array)
        image=bpy.data.images.load(str(path),check_existing=False);images.append(image)
        image.colorspace_settings.name='Non-Color' if role=='rgbd' else 'sRGB'
        image.alpha_mode='CHANNEL_PACKED'
        if diagnostic:
            image_receipts.append({'channel':channel,'variant':chosen,'array':str(array_path),
                'array_sha256':sha(array_path),'png':str(path),'png_sha256':sha(path),
                'source_image_name':binding.get('image_name'),'image_name':image.name,
                'colour_space':image.colorspace_settings.name,'alpha_mode':image.alpha_mode})
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
        receipt={'kind':'local-decoded-delivery-diffuse','mode':mode,'variant':variant,'contract_sha256':record['contract_sha256'],
            'source_materials':source_materials,'minimum_divisor':record['model_contract']['minimum_divisor'],
            'tint_strength':record['model_contract']['tint_strength'],'colour_atlas_bindings':sorted(used_atlases),
            'rgbd_filter':'encoded UNORM bilinear then pow2.2 / max(filtered divisor, model minimum)',
            'tint_filter':'sRGB decode before linear interpolation; influence alpha applied once by existing colour group',
            'display':'shader pow1/2.2 then Raw/None exposure0 gamma1; no second AgX',
            'limits':record['limits']}
        if diagnostic:
            receipt.update(kind='diagnostic-delivery-channel-isolation',accepted=False,source_saved=False,
                channel_selection={channel:selection[channel] for channel in ('colour_atlases','lightmap','tint')
                                   if channel!='tint' or record['bindings'].get('tint')},
                selected_bindings=selected,image_bindings=image_receipts,
                limits='Diagnostic channel isolation only; not final all-decoded validation or production acceptance. '+record['limits'])
        yield receipt
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
