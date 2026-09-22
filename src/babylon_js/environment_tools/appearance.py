"""Saved preview identity, independent of file paths and editor layout.

This verifies combination/capture correspondence, not physical bake freshness.
"""
import hashlib
import json
from pathlib import Path
import bpy
from common import sha

RUNTIME = {'Shadow Suppression', 'Fully Lit Threshold', 'Highlight Preservation'}
UI = {'name','label','location','location_absolute','width','width_hidden','height','dimensions','select',
      'show_options','show_preview','show_texture','hide','use_custom_color','color',
      'warning_propagation','bl_description','bl_label','bl_icon','bl_static_type',
      'bl_width_default','bl_width_min','bl_width_max','bl_height_default',
      'bl_height_min','bl_height_max','bl_idname'}


def preview_identity(material, *, diagnostic=False):
    """Hash shader properties, wiring, non-runtime controls and source-image bytes."""
    image_hashes={}
    def value(v):
        if isinstance(v,(str,int,float,bool)) or v is None:return v
        return list(v)
    def tree_data(tree,ancestors=()):
        if tree.as_pointer() in ancestors:raise ValueError('Recursive preview graph')
        rows=[]
        for n in sorted(tree.nodes,key=lambda n:n.name):
            if n.type=='GROUP_INPUT' and any(s.name in RUNTIME and s.is_linked for s in n.outputs):
                raise ValueError('Runtime metadata controls must not drive the preview shader')
            props={}
            for p in n.bl_rna.properties:
                if p.identifier in UI or p.is_readonly or p.type not in ('BOOLEAN','INT','FLOAT','STRING','ENUM'):continue
                props[p.identifier]=value(getattr(n,p.identifier))
            inputs={}
            for i,s in enumerate(n.inputs):
                if s.name in RUNTIME:
                    if s.is_linked:raise ValueError('Runtime metadata controls must be unlinked')
                    continue
                if hasattr(s,'default_value') and not s.is_linked:inputs[str(i)]=value(s.default_value)
            row={'name':n.name,'type':n.bl_idname,'properties':props,'inputs':inputs}
            if n.type in ('RGB','VALUE'):
                row['outputs']=[value(s.default_value) for s in n.outputs]
            if n.type=='GROUP':row['tree']=tree_data(n.node_tree,ancestors+(tree.as_pointer(),))
            if n.type=='TEX_IMAGE':
                im=n.image
                if im is None or im.source!='FILE' or im.packed_file or im.is_dirty:
                    raise ValueError('Preview provenance requires saved, freshly loaded external images: '+n.name)
                path=Path(bpy.path.abspath(im.filepath)).resolve()
                if path not in image_hashes:image_hashes[path]=sha(path)
                row['image']={'sha256':image_hashes[path],'colour_space':im.colorspace_settings.name,'alpha_mode':im.alpha_mode}
            rows.append(row)
        links=sorted((l.from_node.name,list(l.from_node.outputs).index(l.from_socket),l.to_node.name,list(l.to_node.inputs).index(l.to_socket)) for l in tree.links)
        return {'nodes':rows,'links':links}
    payload=tree_data(material.node_tree)
    if diagnostic:return payload
    return hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def verify_combination(material,combined,provenance):
    path=Path(provenance)
    if not path.is_file():raise ValueError('Combined-lightmap provenance missing; run combine --reuse-lightmaps with original ownership first')
    record=json.loads(path.read_text(encoding='utf8'))
    if record.get('schema_version')!=1 or record.get('combined_sha256')!=sha(combined):
        raise ValueError('Combined-lightmap provenance does not match image bytes')
    if record.get('preview_sha256')!=preview_identity(material):
        raise ValueError('Preview controls, graph or source maps changed; recombine before baked capture')
    return record
