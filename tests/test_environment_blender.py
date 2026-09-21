"""Real ENV helper integration against a supplied linear panorama."""
import bpy,json,sys
from pathlib import Path
import numpy as np
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
import babylon_js
from babylon_js import env_export as env
from babylon_js import ktx_conversion as core
args=sys.argv[sys.argv.index('--')+1:]
out=Path(args[0]).resolve();source=Path(args[1]).resolve();out.mkdir(parents=True,exist_ok=False)
helper=ROOT/'tools/env-converter/convert.cjs'
babylon_js.register()
props=bpy.ops.export.bjs.get_rna_type().properties
assert props['export_environment'].default is False
assert props['environment_size'].default=='512'
assert props['environment_highlight_gain'].default==1
assert not env.find_converter(str(out/'missing.cjs'))[0]
assert not env.infer_capture(bpy.context)
bpy.context.scene.world['bjs_environment_image']=str(source)
assert env.infer_capture(bpy.context)==str(source)
assert env.find_converter(str(helper))[0]
report=env.prepare_capture(source,out/'source.hdr',0,1,1)
assert core.sha(source)==report['source_sha256']
# A failed ENV conversion must not publish the staged model over existing files.
from babylon_js.json_exporter import JsonExporter
delivery=out/'rollback';delivery.mkdir();target=delivery/'fixture.babylon'
target.write_text('previous accepted model')
with patch.object(env,'export_environment',side_effect=RuntimeError('injected ENV failure')):
    exporter=JsonExporter()
    try:
        exporter.execute(bpy.context,str(target),list(bpy.context.scene.objects),
            ktx_options=dict(convert_materials=False,environment=dict(converter=str(helper),image=str(source),size=128)))
    except RuntimeError:
        pass
assert target.read_text()=='previous accepted model'
# RGBE HDR roundtrip preserves >1, no accidental gamma conversion or clipping.
rgb=np.tile(np.array([.03125,2.,16.],np.float32),(16,32,1))
env.write_hdr(out/'range.hdr',rgb)
from babylon_js.skybox_export import read_panorama
read,is_hdr=read_panorama(out/'range.hdr')
assert is_hdr and np.allclose(read,rgb,rtol=.02,atol=.07)
info=env.prepare_capture(out/'range.hdr',out/'boost.hdr',0,2,1)
boosted,_=read_panorama(out/'boost.hdr')
assert np.max(boosted)>np.max(read)*1.9
model={'meshes':[{'name':'original','id':'hasenv'}]}
prepared=out/'prepared';prepared.mkdir()
result=env.export_environment(bpy.context,model,out,prepared,
    dict(converter=str(helper),image=str(source),size=128))
assert result['status']=='passed' and result['converter']['reloaded']
assert len([m for m in model['meshes'] if m['name']=='hasenv'])==1
assert model['meshes'][1]['id']!='hasenv'
assert core.sha(source)==report['source_sha256']
(out/'result.json').write_text(json.dumps(result,indent=2))
print('ENV_TEST_PASSED',str(out),flush=True)
