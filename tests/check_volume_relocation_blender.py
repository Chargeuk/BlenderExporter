"""Focused real-scene relocation guards; run against a disposable clean candidate."""
import copy
import json
from pathlib import Path
import sys
import bpy
from babylon_js.environment_lighting_volume import validate_record,validate_source

receipt=Path(sys.argv[sys.argv.index('--')+1]).resolve()
record=validate_record(json.loads(receipt.read_text()))
passed=[]

def check():
    validate_source(bpy.context,record,receipt_path=receipt)

def reject(name,change,restore):
    change()
    try:
        try:check()
        except ValueError:passed.append(name)
        else:raise AssertionError('Tampered state accepted: '+name)
    finally:restore()
    check()

check();passed.append('valid portable source')
original_hash=record['source']['sceneSha256']
reject('changed source hash',lambda:record['source'].update(sceneSha256='0'*64),lambda:record['source'].update(sceneSha256=original_hash))
material=next(m for m in bpy.data.materials if m.use_nodes and any(n.type=='BSDF_PRINCIPLED' for n in m.node_tree.nodes))
node=next(n for n in material.node_tree.nodes if n.type=='BSDF_PRINCIPLED')
value=node.inputs['Roughness'].default_value
reject('physical material graph',lambda:setattr(node.inputs['Roughness'],'default_value',.123),lambda:setattr(node.inputs['Roughness'],'default_value',value))
obj=next(o for o in bpy.context.scene.objects if o.type=='MESH' and o.data.vertices and o.material_slots)
point=obj.data.vertices[0];coordinate=point.co.copy()
reject('physical geometry',lambda:setattr(point,'co',coordinate+__import__('mathutils').Vector((.1,0,0))),lambda:setattr(point,'co',coordinate))
slot=obj.material_slots[0];old=slot.material
alternative=next(m for m in bpy.data.materials if m!=old)
reject('physical material assignment',lambda:setattr(slot,'material',alternative),lambda:setattr(slot,'material',old))
image=next(i for i in bpy.data.images if i.source=='FILE')
old_path=image.filepath;other=next(i for i in bpy.data.images if i.source=='FILE' and i!=image)
reject('image content binding',lambda:setattr(image,'filepath',other.filepath),lambda:setattr(image,'filepath',old_path))
if bpy.data.lights:
    light=bpy.data.lights[0];energy=light.energy
    reject('physical light energy',lambda:setattr(light,'energy',energy+1),lambda:setattr(light,'energy',energy))
check()
print('VOLUME_RELOCATION_GUARDS_PASSED '+json.dumps(dict(passed=len(passed),checks=passed)),flush=True)
