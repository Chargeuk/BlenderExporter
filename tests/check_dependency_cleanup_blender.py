"""Validate a disposable cleanup derivative and reject protected-state edits in memory."""
import hashlib
import json
from pathlib import Path
import sys
import bpy
from mathutils import Vector
from babylon_js.environment_lighting_volume import validate_record,validate_source
from babylon_js.environment_tools.volume_relocation import scene_identity

receipt=Path(sys.argv[sys.argv.index('--')+1]).resolve()
record=validate_record(json.loads(receipt.read_text(encoding='utf8')))
proof=json.loads((receipt.parent/record['relocation']['proof']).read_text(encoding='utf8'))
cleanup=proof['dependencyCleanup'];passed=[]


def check(*,diagnostic=True):
    try:validate_source(bpy.context,record,receipt_path=receipt)
    except ValueError as error:
        if diagnostic:
            evidence=dict(error=str(error))
            trace=error.__traceback__
            while trace is not None:
                if trace.tb_frame.f_code.co_name=='validate_dependency_cleanup_lineage':
                    state=trace.tb_frame.f_locals;key=state.get('key');path=state.get('path')
                    evidence.update(key=key,path=str(path),exists=path.is_file() if path else None,
                        expected=state.get('cleanup',{}).get(str(key)+'Sha256'),
                        actual=hashlib.sha256(path.read_bytes()).hexdigest() if path and path.is_file() else None)
                trace=trace.tb_next
            print('CLEANUP_UNEXPECTED_RESTORE_FAILURE '+json.dumps(evidence),flush=True)
        raise


def reject(name,change,restore):
    change()
    try:
        try:check(diagnostic=False)
        except ValueError:passed.append(name)
        else:raise AssertionError('Tampered cleanup source accepted: '+name)
    finally:restore()
    check()


check();passed.append('valid derivative and immutable parent lineage')
assert scene_identity(bpy.context)==cleanup['canonicalSceneIdentityBefore']
assert all(bpy.data.images.get(row['source']) is None for row in cleanup['imageRemaps'])
passed.append('declared legacy images absent')
material=next(bpy.data.materials[name] for name in cleanup['protectedMaterials']
              if any(node.type=='BSDF_PRINCIPLED' for node in bpy.data.materials[name].node_tree.nodes))
node=next(node for node in material.node_tree.nodes if node.type=='BSDF_PRINCIPLED')
# The RM texture drives Roughness, so its unused default is intentionally outside
# the fingerprint. Change the effective, unlinked IOR control instead.
assert node.inputs['Roughness'].is_linked, 'Inspect an unexpected unlinked Roughness fingerprint gap'
socket=node.inputs['IOR'];assert not socket.is_linked, 'Choose an effective unlinked physical control'
old=socket.default_value
print('CLEANUP_PHYSICAL_GUARD '+json.dumps(dict(material=material.name,roughness_linked=True,
      mutated_input=socket.name,input_linked=socket.is_linked,previous_value=old)),flush=True)
reject('protected physical graph',lambda:setattr(socket,'default_value',2.0 if old!=2.0 else 1.25),
       lambda:setattr(socket,'default_value',old))
obj=next(obj for obj in bpy.data.objects if obj.type=='MESH' and obj.data.vertices)
point=obj.data.vertices[0];coordinate=point.co.copy()
reject('geometry',lambda:setattr(point,'co',coordinate+Vector((.1,0,0))),lambda:setattr(point,'co',coordinate))
mesh=next(mesh for mesh in bpy.data.meshes if mesh.uv_layers and len(mesh.uv_layers[0].data))
uv=mesh.uv_layers[0].data[0];old_uv=uv.uv.copy()
reject('UV coordinates',lambda:setattr(uv,'uv',old_uv+Vector((.01,0))),lambda:setattr(uv,'uv',old_uv))
images=[image for image in bpy.data.images if image.source=='FILE']
image=images[0];old_path=image.filepath
other=next(other for other in images if Path(bpy.path.abspath(other.filepath)).resolve()!=Path(bpy.path.abspath(old_path)).resolve())
reject('image content binding',lambda:setattr(image,'filepath',other.filepath),lambda:setattr(image,'filepath',old_path))
if bpy.data.lights:
    light=bpy.data.lights[0];energy=light.energy
    reject('light state',lambda:setattr(light,'energy',energy+1),lambda:setattr(light,'energy',energy))
if bpy.data.cameras:
    camera=bpy.data.cameras[0];lens=camera.lens
    reject('camera state',lambda:setattr(camera,'lens',lens+1),lambda:setattr(camera,'lens',lens))
old_hash=record['source']['sceneSha256']
reject('source hash',lambda:record['source'].update(sceneSha256='0'*64),lambda:record['source'].update(sceneSha256=old_hash))
parent=receipt.parent/cleanup['parentProof'];parent_bytes=parent.read_bytes()
reject('parent evidence hash',lambda:parent.write_bytes(b'{}'),lambda:parent.write_bytes(parent_bytes))
check()
print('DEPENDENCY_CLEANUP_GUARDS_PASSED '+json.dumps(dict(passed=len(passed),checks=passed)),flush=True)
