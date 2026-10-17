"""Finish the real Blender fixture using the separate NumPy/SciPy processing runtime."""
import json
import sys
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src/babylon_js/environment_tools'))
from common import sha
from ownership import rasterize
from receiver_response import factor_from_captures, apply_response, save_new_array

out = Path(sys.argv[1]).resolve()
stage = out/'response-capture'
capture_result = json.loads((out/'capture-result.json').read_text())
assert capture_result['status'] == 'capture-passed'
assert capture_result['linked_metallic_restored'] and capture_result['shared_material_preserved']
assert sha(out/'fixture.blend') == capture_result['source_sha256']
assert sha(out/'native_direct.npy') == capture_result['native_direct_sha256']
assert sha(out/'native_islands.json') == capture_result['native_islands_sha256']
record = json.loads((stage/'capture.provenance.json').read_text())
for name, digest in record['files'].items(): assert sha(stage/name) == digest
native = np.load(out/'native_direct.npy', allow_pickle=False)
catalogue = json.loads((out/'native_islands.json').read_text())
labels, ownership = rasterize(native, catalogue)
captured = json.loads((stage/'islands.json').read_text())
patch_rows = json.loads((stage/'patches.json').read_text())
patches = [dict(row, pixels=np.load(stage/row['file'], allow_pickle=False)) for row in patch_rows]
base = np.load(stage/'source_base_colour.npy', allow_pickle=False)
diffuse = np.load(stage/'diffuse_colour.npy', allow_pickle=False)
factor, response, report = factor_from_captures(base, diffuse, labels, catalogue, captured, patches, ['Capped'])
uncapped_mask = np.isin(labels, [r['id'] for r in catalogue if r['object'] == 'Uncapped'])
capped_mask = np.isin(labels, [r['id'] for r in catalogue if r['object'] == 'Capped'])
assert uncapped_mask.any() and capped_mask.any()
np.testing.assert_array_equal(response[uncapped_mask, :3], diffuse[uncapped_mask, :3])
assert np.mean(response[capped_mask, :3]) > np.mean(diffuse[capped_mask, :3]) * 1.1
np.testing.assert_allclose(factor[labels > 0, :3], response[labels > 0, :3]/base[labels > 0, :3], rtol=1e-6)
assert native[labels > 0, :3].max() > 0, 'Application must exercise actual nonzero radiance'
before = native.copy()
derived, filled = apply_response(native, factor, labels)
np.testing.assert_array_equal(native, before)
np.testing.assert_array_equal(derived[:, :, 3], native[:, :, 3])
np.testing.assert_array_equal(filled[labels > 0], derived[labels > 0])
np.testing.assert_array_equal(derived[labels > 0, :3], native[labels > 0, :3]*factor[labels > 0, :3])
save_new_array(out/'verified-factor.npy', factor)
save_new_array(out/'verified-response-owned.npy', response)
save_new_array(out/'verified-derived-owned.npy', derived)
assert sha(out/'native_direct.npy') == capture_result['native_direct_sha256']
assert sha(out/'fixture.blend') == capture_result['source_sha256']
(out/'result.json').write_text(json.dumps({'status': 'passed', 'capture_operations': 2,
    'native_coverage_owned_pixels': ownership['owned_pixels'], 'response': report,
    'linked_metallic_restored': True, 'shared_material_preserved': True,
    'native_transport_preserved': True, 'alpha_preserved': True,
    'source_sha256': capture_result['source_sha256'], 'fixture_preflight': 'World/preview bindings excluded',
    'runtime_split': 'Blender NumPy capture; external NumPy/SciPy ownership and application'}, indent=2))
print('RECEIVER_RESPONSE_FIXTURE_PASSED', str(out), flush=True)
