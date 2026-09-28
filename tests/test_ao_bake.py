"""Factory Blender regression for AO transport, zero-margin ownership and geometry reuse."""
import sys,json
from pathlib import Path
import bpy,numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'src/babylon_js/environment_tools')]
from ao import bake,geometry_identity
out=Path(sys.argv[sys.argv.index('--')+1]);out.mkdir(parents=True,exist_ok=False)
cube=bpy.data.objects['Cube'];cube.data.uv_layers[0].name='SimpleBake'
bpy.ops.mesh.primitive_plane_add(size=8,location=(0,0,-1.01));blocker=bpy.context.object;blocker.name='Blocker'
c={'size':64,'device':'CPU','uv':'SimpleBake','receivers':{'names':['Cube']},'contributors':{'names':['Blocker']},
   'ao':{'enabled':True,'distance':1.,'samples':32,'node_samples':16}}
original=geometry_identity(c)
cube.location.x+=.01;assert geometry_identity(c)['geometry_sha256']!=original['geometry_sha256'];cube.location.x-=.01
cube.data.uv_layers[0].data[0].uv.x+=.001
assert geometry_identity(c)['geometry_sha256']!=original['geometry_sha256'];cube.data.uv_layers[0].data[0].uv.x-=.001
stage=out/'ao';stage.mkdir();report=bake(c,out,stage)
raw=np.load(stage/'ao_raw.npy');mask=raw[:,:,3]>0
assert mask.any() and (~mask).any(),'Emission clear destroyed original coverage'
assert np.isin(raw[:,:,3],[0,1]).all()
assert np.max(raw[:,:,:3][mask])-np.min(raw[:,:,:3][mask])>.2,'AO did not capture nearby geometry'
assert np.max(abs(raw[:,:,0]-raw[:,:,1]))<1e-6
assert np.max(abs(raw[:,:,0]-raw[:,:,2]))<1e-6
(out/'result.json').write_text(json.dumps(dict(report,status='passed',unowned_pixels=int((~mask).sum()),geometry_change_rejected=True),indent=2))
print('AO_BAKE_TEST_PASSED')
