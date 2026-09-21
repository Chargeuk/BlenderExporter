import bpy, json
from collections import Counter
from pathlib import Path
s=bpy.context.scene
info={'file':bpy.data.filepath,'roles':dict(Counter((o.type,o.get('role','none')) for o in s.objects))}
info['roles']={str(k):v for k,v in info['roles'].items()}
info['empties']=[{'name':o.name,'parent':o.parent.name if o.parent else None,'props':dict(o.items())} for o in s.objects if o.type=='EMPTY']
info['lights']=[{'name':o.name,'role':o.get('role'),'type':o.data.type,'energy':o.data.energy} for o in s.objects if o.type=='LIGHT']
info['materials']=[{'name':m.name,'nodes':[n.bl_idname for n in m.node_tree.nodes],'links':[(l.from_node.bl_idname,l.from_socket.name,l.to_node.bl_idname,l.to_socket.name) for l in m.node_tree.links]} for m in bpy.data.materials if m.users and m.use_nodes and 'shared' in m.name.lower()]
info['helpers']=[{'name':o.name,'parent':o.parent.name if o.parent else None,'role':o.get('role'),'hide':o.hide_get(),'render_hidden':o.hide_render} for o in s.objects if o.type=='MESH' and o.get('role') not in ['VISIBLE_DELIVERY_AND_BAKE_RECEIVER','BAKE_ONLY_CONTRIBUTOR','RETAINED_SOURCE']][:45]
Path('artifacts').mkdir(exist_ok=True)
Path('artifacts/bedroom_inventory.json').write_text(json.dumps(info,indent=2,default=str),encoding='utf-8')
print(json.dumps(info,default=str),flush=True)
