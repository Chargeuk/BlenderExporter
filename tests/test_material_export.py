"""Exercise export-only PBR calibration through real serialization and staging."""
import bpy,json,sys,math
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
import babylon_js
from babylon_js.json_exporter import JsonExporter
babylon_js.register()
out=Path(sys.argv[sys.argv.index('--')+1]).resolve();out.mkdir(parents=True,exist_ok=False)
s=bpy.context.scene;s.world.usePBRMaterials=True;s.world.inlineTextures=False
cube=s.objects['Cube'];flat=cube.copy();flat.data=cube.data.copy();s.collection.objects.link(flat);flat.name='Flat';flat.location.x=3
im=bpy.data.images.new('RM',width=16,height=16);im.generated_color=(0,.8,.6,1)
im.filepath_raw=str(out/'rm.png');im.file_format='PNG';im.save();im.colorspace_settings.name='Non-Color'
materials=[]
for ob,name,packed in [(cube,'Packed',True),(flat,'Flat',False)]:
    m=bpy.data.materials.new(name);m.use_nodes=True;ob.data.materials.clear();ob.data.materials.append(m)
    p=next(n for n in m.node_tree.nodes if n.type=='BSDF_PRINCIPLED')
    p.inputs['Metallic'].default_value=.6;p.inputs['Roughness'].default_value=.8
    if packed:
        tex=m.node_tree.nodes.new('ShaderNodeTexImage');tex.image=im
        sep=m.node_tree.nodes.new('ShaderNodeSeparateColor')
        m.node_tree.links.new(tex.outputs['Color'],sep.inputs['Color'])
        m.node_tree.links.new(sep.outputs['Green'],p.inputs['Roughness']);m.node_tree.links.new(sep.outputs['Blue'],p.inputs['Metallic'])
    materials.append((m,p))
before=[(p.inputs['Metallic'].default_value,p.inputs['Roughness'].default_value,len(m.node_tree.links)) for m,p in materials]
def run(name,options=None,staged=False):
    e=JsonExporter();e.execute(bpy.context,str(out/(name+'.babylon')),[cube,flat],
        ktx_options=dict(convert_materials=False) if staged else None,material_options=options)
    assert not e.fatalError,e.fatalError
    data=json.loads((out/(name+'.babylon')).read_text());return {m['name']:m for m in data['materials']}
def close(a,b):assert math.isclose(a,b,abs_tol=1e-5),(a,b)
for name,options,staged,expected in [('default',None,False,(.5,.4)),('staged',dict(metallic=.7,roughness=.9),True,(.7,.9)),('neutral',dict(metallic=1,roughness=1),False,(1,1))]:
    result=run(name,options,staged)
    close(result['Packed']['metallic'],expected[0]);close(result['Packed']['roughness'],expected[1])
    close(result['Flat']['metallic'],.6*expected[0]);close(result['Flat']['roughness'],.8*expected[1])
    assert result['Packed']['useMetallnessFromMetallicTextureBlue']
    assert result['Packed']['useRoughnessFromMetallicTextureGreen']
assert before==[(p.inputs['Metallic'].default_value,p.inputs['Roughness'].default_value,len(m.node_tree.links)) for m,p in materials]
s.world.usePBRMaterials=False
standard=run('standard');assert 'metallic' not in standard['Flat'];close(standard['Flat']['specularPower'],25.6)
s.world.usePBRMaterials=True
bpy.ops.object.select_all(action='DESELECT');cube.select_set(True);flat.select_set(True)
assert bpy.ops.export.bjs(filepath=str(out/'operator.babylon'),export_selected=True,
    material_metallic_multiplier=.2,material_roughness_multiplier=.3)=={'FINISHED'}
parsed=json.loads((out/'operator.babylon').read_text());packed=next(m for m in parsed['materials'] if m['name']=='Packed')
close(packed['metallic'],.2);close(packed['roughness'],.3)
target=out/'preserved.babylon';target.write_text('previous')
bad=JsonExporter();bad.execute(bpy.context,str(target),[cube],material_options={'metallic':float('nan')})
assert bad.fatalError and target.read_text()=='previous'
props=bpy.ops.export.bjs.get_rna_type().properties
close(props['material_metallic_multiplier'].default,.5);close(props['material_roughness_multiplier'].default,.4)
(out/'result.json').write_text(json.dumps(dict(status='passed',checks=['textured defaults','untextured factors','neutral override','staged explicit overrides','unchanged source nodes and links','standard materials unaffected','real operator controls','invalid settings preserve output']),indent=2))
print('MATERIAL_EXPORT_TEST_PASSED',str(out))
