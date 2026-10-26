"""Real Blender glass-rule serialization and saved-source preservation fixture.

Use fresh factory-startup Blender, --python this_file -- --helper recipe_cli.py
--output FRESH_DIRECTORY. Works against source or packaged installation; no render.
"""
import argparse
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys
import bpy

parser=argparse.ArgumentParser()
parser.add_argument('--helper',type=Path,required=True)
parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args(sys.argv[sys.argv.index('--')+1:])
helper=args.helper.resolve();out=args.output.resolve()
assert not out.exists(),'Fresh fixture output required'
out.mkdir(parents=True)
sys.path.insert(0,str(helper.parent.parent.parent))
import babylon_js
try:
    bpy.ops.export.bjs.get_rna_type()
except (KeyError,RuntimeError):
    babylon_js.register()
assert tuple(babylon_js.bl_info['version'])==(3,3,37)
assert Path(babylon_js.__file__).resolve().parent==helper.parent.parent
sys.path.insert(0,str(helper.parent))
spec=importlib.util.spec_from_file_location('glass_fixture_recipe',helper)
recipe=importlib.util.module_from_spec(spec);spec.loader.exec_module(recipe)
from babylon_js.json_exporter import JsonExporter

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def close(a,b):assert math.isclose(a,b,rel_tol=0,abs_tol=1e-6),(a,b)

scene=bpy.context.scene
scene.world.usePBRMaterials=True;scene.world.inlineTextures=False
cube=scene.objects['Cube']
mat=bpy.data.materials.new('FixtureGlass');mat.use_nodes=True
cube.data.materials.clear();cube.data.materials.append(mat)
node=next(n for n in mat.node_tree.nodes if n.type=='BSDF_PRINCIPLED')
node.inputs['Alpha'].default_value=1
node.inputs['Transmission Weight'].default_value=1
source=out/'physical-source.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(source))
source_sha=sha(source)
results=[]
for name,override in [('legacy',None),('explicit',.18),('zero',0),('one',1)]:
    node.inputs['Alpha'].default_value=.43 if override is None else 1
    original=float(node.inputs['Alpha'].default_value)
    rule=dict(material=mat.name,transparency_mode=2)
    if override is not None:rule['alpha']=override
    facts=recipe.prepare_glass_material(mat,rule)
    expected=original if override is None else override
    close(facts['original_alpha'],original);close(facts['staged_alpha'],expected)
    target=out/(name+'.babylon')
    exporter=JsonExporter();exporter.execute(bpy.context,str(target),[cube])
    assert not exporter.fatalError,exporter.fatalError
    data=json.loads(target.read_text());serialized=next(m for m in data['materials'] if m['name']==mat.name)
    close(serialized.get('alpha',1),expected)
    assert serialized['transparencyMode']==2
    assert sha(source)==source_sha
    results.append(dict(case=name,preparation=facts,serialized_alpha=serialized.get('alpha',1),
                        model_sha256=sha(target)))

node.inputs['Alpha'].default_value=1
linked=mat.node_tree.nodes.new('ShaderNodeValue')
link=mat.node_tree.links.new(linked.outputs[0],node.inputs['Alpha'])
before=(tuple(node.inputs['Base Color'].default_value),float(node.inputs['Alpha'].default_value),
        mat.transparencyMode,len(mat.node_tree.links))
try:
    recipe.prepare_glass_material(mat,dict(alpha=.18,transparency_mode=2))
    raise AssertionError('Linked Alpha override was accepted')
except ValueError as error:
    assert 'linked Alpha' in str(error)
assert before==(tuple(node.inputs['Base Color'].default_value),float(node.inputs['Alpha'].default_value),
                mat.transparencyMode,len(mat.node_tree.links))
mat.node_tree.links.remove(link)
assert sha(source)==source_sha
bpy.ops.wm.open_mainfile(filepath=str(source))
saved=bpy.data.materials['FixtureGlass']
saved_node=next(n for n in saved.node_tree.nodes if n.type=='BSDF_PRINCIPLED')
assert float(saved_node.inputs['Alpha'].default_value)==1 and not saved_node.inputs['Alpha'].is_linked
assert sha(source)==source_sha
report=dict(status='passed',checks=['legacy serialization','explicit.18 serialization','zero serialization',
    'one serialization','linked Alpha rejection without mutation','saved source byte identity and reopened Alpha1'],
    helper=str(helper),helper_sha256=sha(helper),source_sha256=source_sha,cases=results,rendered=False)
(out/'result.json').write_text(json.dumps(report,indent=2)+'\n')
print('GLASS_PREPARATION_BLENDER_FIXTURE_PASS',json.dumps(report),flush=True)
