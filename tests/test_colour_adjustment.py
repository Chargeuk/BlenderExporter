"""Native Blender tests for colour-only adjustment, AgX inputs and export contract."""
import sys,json
from pathlib import Path
import bpy,numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from babylon_js import colour_adjustment as ca
from babylon_js.lighting_controls import create_preview_material
from babylon_js.agx_lightmap import write_exr,bake_albedo,combine_saved_controls,compensate
out=Path(sys.argv[sys.argv.index('--')+1]);out.mkdir(parents=True,exist_ok=True)
def image(name,rgb):
    a=np.ones((16,16,4),np.float32);a[...,:3]=rgb
    path=out/(name+'.exr');write_exr(path,a)
    im=bpy.data.images.load(str(path));im.colorspace_settings.name='sRGB' if name=='colour' else 'Non-Color'
    return im
preview,node=create_preview_material(image('colour',1),image('direct',2),image('indirect',1),image('ids',1))
node.inputs['Shadow Lift'].default_value=0
before=combine_saved_controls(bpy.context,16,out/'before.exr')
im=ca.create_image(out/'correction.png',512);layer=ca.attach(bpy.context.scene,im)
assert ca.settings(bpy.context.scene)['strength']==1
assert np.array_equal(before,combine_saved_controls(bpy.context,16,out/'neutral.exr'))
# A simple UV2 plane and physical albedo provide exact colour/alpha expectations.
bpy.ops.object.select_all(action='DESELECT');bpy.ops.mesh.primitive_plane_add()
obj=bpy.context.object;obj.name='Colour test plane';obj.data.uv_layers.new(name='SimpleBake')
for d,uv in zip(obj.data.uv_layers[1].data,[(0,0),(1,0),(1,1),(0,1)]):d.uv=uv
mat=bpy.data.materials.new('Physical');mat.use_nodes=True;obj.data.materials.append(mat)
base=next(n for n in mat.node_tree.nodes if n.type=='BSDF_PRINCIPLED').inputs['Base Color'];base.default_value=(.4,.3,.2,1)
raw=bake_albedo(bpy.context,[obj],out/'raw.exr',16)
results=[]
for alpha in (0,.5,1):
    report=ca.tint_objects(im,[obj],(.5,1.2,1.8),alpha)
    config=ca.settings(bpy.context.scene)
    got=bake_albedo(bpy.context,[obj],out/f'tinted-{alpha}.exr',16,colour_adjustment=config)
    codes=ca.read_codes(im)[256,256];factor=1+(2*ca.srgb_decode(codes[:3])-1)*codes[3]
    assert np.max(abs(got[...,:3]-raw[...,:3]*factor))<1e-5,(alpha,got[8,8],factor)
    assert np.array_equal(before,combine_saved_controls(bpy.context,16,out/'illumination.exr')),'Tint leaked into illumination'
    assert np.array_equal(base.default_value[:],(.4000000059604645,.30000001192092896,.20000000298023224,1))
    results.append(dict(alpha=alpha,factor=factor.tolist(),**report))
node.inputs[ca.STRENGTH].default_value=0
assert np.max(abs(bake_albedo(bpy.context,[obj],out/'disabled.exr',16,colour_adjustment=ca.settings(bpy.context.scene))-raw))<1e-6
node.inputs[ca.STRENGTH].default_value=.65
saved=out/'fixture.blend';bpy.ops.wm.save_as_mainfile(filepath=str(saved));bpy.ops.wm.open_mainfile(filepath=str(saved))
assert abs(ca.settings(bpy.context.scene)['strength']-.65)<1e-6
# Same export settings as the saved node, but explicitly disabled stays absent.
from types import SimpleNamespace
op=SimpleNamespace(colour_adjustment_enabled=False,colour_adjustment_strength=0)
ca.prefill(op,bpy.context)
assert op.colour_adjustment_enabled and abs(op.colour_adjustment_strength-.65)<1e-6
assert ca.resolve(bpy.context.scene,{'colour_adjustment_enabled':False}) is None
# Downsampling weights colour by influence and preserves transparent neutral cells.
big=np.zeros((1024,1024,4),np.float32);big[...,:3]=ca.srgb_encode(np.array(.5))
big[::2,::2,:3]=ca.srgb_encode(np.array([.2,.6,.9]));big[::2,::2,3]=1
ca.save_codes(out/'large.png',big);large=ca.load_image(out/'large.png')
small=ca.prepare_image(large,out/'small.png',512);smallcodes=ca.read_codes(small)
assert np.max(abs(smallcodes[...,3]-.25))<1/255
assert np.max(abs(ca.srgb_decode(smallcodes[...,:3])-[.2,.6,.9]))<.005
# Metadata is generated only for the intended marker and rewritten by KTX planning.
from babylon_js.ktx_export import make_config
from babylon_js.ktx_conversion import load_plan
raw=out/'fixture.babylon';marker='lightmap_fixture';lm=out/'before.exr'
raw.write_text(json.dumps({'meshes':[{'name':marker,'id':'unchanged-id'}],'materials':[]}))
options=dict(lightmap=str(lm),lightmap_marker=marker,lightmap_agx=False,lightmap_encoding='rgbd-v1',colour_adjustment_enabled=True,colour_adjustment_size=512,colour_adjustment_strength=.3)
count=len(bpy.data.images)
config=make_config(raw,out,options,bpy.context,[])
assert len(bpy.data.images)==count
path=out/'conversion.json';path.write_text(json.dumps(config));plan=load_plan(path)
meta=plan['models'][0]['data']['meshes'][0]['metadata']['kadshowColourAdjustment']
assert meta==dict(version=1,texture='fixture_colour_adjustment.ktx2',coordinatesIndex=1,encoding=ca.ENCODING,strength=.3),meta
assert plan['models'][0]['data']['meshes'][0]['id']=='unchanged-id'
raw.write_text(json.dumps({'meshes':[{'name':marker,'id':'unchanged-id'}],'materials':[]}))
options['colour_adjustment_enabled']=False
config=make_config(raw,out,options,bpy.context,[])
assert len(config['textures'])==1
assert 'kadshowColourAdjustment' not in json.loads(raw.read_text())['meshes'][0].get('metadata',{})
(out/'result.json').write_text(json.dumps(dict(status='passed',tests=results,illumination_unchanged=True,save_reopen=True),indent=2))
print('COLOUR_ADJUSTMENT_TEST_PASSED',flush=True)
