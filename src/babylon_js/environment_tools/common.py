import hashlib
import json
import time
from pathlib import Path


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''): h.update(block)
    return h.hexdigest()


def save_json(path, value):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, indent=2), encoding='utf8')
    # Windows readers and antivirus can briefly hold the destination open.
    for attempt in range(20):
        try:
            tmp.replace(path)
            break
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.05 * (attempt + 1))


def image_dependency_scope(c):
    scope = c.get('image_dependency_scope', 'all')
    if scope not in ('all', 'participants'):
        raise ValueError('image_dependency_scope must be all or participants')
    return scope


def physical_world_routing(c):
    routing = c.get('physical_world_routing', 'direct')
    if routing not in ('direct', 'camera-separated'):
        raise ValueError('physical_world_routing must be direct or camera-separated')
    return routing


def load_config(path):
    path = Path(path).resolve()
    c = json.loads(path.read_text(encoding='utf8'))
    if c.get('schema_version') != 1: raise ValueError('Expected environment schema_version 1')
    c['_root'] = str(path.parent)
    c['_config'] = str(path)
    c['_source'] = str((path.parent / c['blend']).resolve())
    if not Path(c['_source']).is_file(): raise ValueError('Saved source .blend is missing')
    size = c.get('size', 2048)
    if not isinstance(size, int) or size < 16 or size > 8192: raise ValueError('Invalid square atlas size')
    c['size'] = size
    if c.get('quality', 'final') not in ('preview', 'final'): raise ValueError('quality must be preview or final')
    c.setdefault('samples', {'DIRECT': 128, 'INDIRECT': 2048})
    if any(not isinstance(c['samples'].get(p), int) or c['samples'][p] < 1 for p in ('DIRECT', 'INDIRECT')):
        raise ValueError('Positive DIRECT and INDIRECT sample counts are required')
    if c.get('quality', 'final') == 'final' and c['samples']['INDIRECT'] != 2048:
        raise ValueError('Final indirect bake requires exactly 2048 fixed samples; label experiments preview')
    if not 0 <= c.get('metallic_cap', .8) <= 1: raise ValueError('Invalid receiving metallic cap')
    c.setdefault('preserve_metal_diffuse_weight', False)
    if type(c['preserve_metal_diffuse_weight']) is not bool:
        raise ValueError('preserve_metal_diffuse_weight must be boolean')
    for key in ('direct', 'indirect', 'island_ids', 'combined', 'colour'):
        if key not in c.get('images', {}): raise ValueError('Missing image binding: ' + key)
    if c.get('ao',{}).get('enabled',False):
        ao=c['ao']
        if not isinstance(ao.get('distance'),(int,float)) or not 0<ao['distance']<10000:
            raise ValueError('AO requires an explicit positive distance in scene units')
        for key in ('samples','node_samples'):
            if not isinstance(ao.get(key),int) or not 1<=ao[key]<=4096:raise ValueError('AO requires explicit positive '+key)
        if 'ao' not in c['images']:raise ValueError('Missing image binding: ao')
    from receiver_response import validate_settings
    validate_settings(c)
    from physical_colour_adjustment import validate_settings as validate_physical_colour, validate_scene
    validate_physical_colour(c)
    validate_scene(c)
    image_dependency_scope(c)
    physical_world_routing(c)
    from camera_views import validate_camera_set
    validate_camera_set(c)
    from native_ownership import ownership_method
    ownership_method(c)
    from native_recombine import finishing_overrides, appearance_settings
    finishing_overrides(c);appearance_settings(c)
    return c


def normalize_metal_diffuse(weighted, base, *, base_epsilon=1e-8, residual_tolerance=1e-6):
    """Remove only EMIT base colour; retain the native diffuse closure weight.

    COLOR-bearing DIFFUSE alpha is opaque even outside UV charts. Only the
    matching zero-margin native EMIT alpha can define receiver coverage.
    """
    import numpy as np
    weighted = np.asarray(weighted, dtype=np.float32)
    base = np.asarray(base, dtype=np.float32)
    if weighted.shape != base.shape or base.ndim != 3 or base.shape[2] != 4:
        raise ValueError('Weighted diffuse and local EMIT base must be matching RGBA')
    if not np.isfinite(weighted).all() or not np.isfinite(base).all():
        raise ValueError('Non-finite weighted diffuse or local EMIT base')
    alpha = base[..., 3]
    if not np.isin(alpha, (0, 1)).all():
        raise ValueError('Local EMIT coverage must be binary')
    mask = alpha == 1
    if not mask.any():
        raise ValueError('Local EMIT coverage is empty')
    colour = base[..., :3]
    if (colour[mask] < 0).any():
        raise ValueError('Negative local EMIT base colour')
    protected = mask[..., None] & (colour <= base_epsilon)
    residual = np.abs(weighted[..., :3][protected])
    if (residual > residual_tolerance).any():
        raise ValueError('Weighted diffuse has energy in a zero/near-zero base channel')
    result = np.zeros_like(base)
    valid = mask[..., None] & (colour > base_epsilon)
    np.divide(weighted[..., :3], colour, out=result[..., :3], where=valid)
    result[..., 3] = alpha
    if not np.isfinite(result).all():
        raise ValueError('Non-finite base-divided diffuse')
    return result, {'owned_pixels': int(mask.sum()), 'off_footprint_pixels': int((~mask).sum()),
                    'coverage_source': 'local native zero-margin EMIT alpha',
                    'base_epsilon': base_epsilon, 'residual_tolerance': residual_tolerance,
                    'protected_base_channels': int(protected.sum()),
                    'protected_residual_max': float(residual.max()) if residual.size else 0.0}
