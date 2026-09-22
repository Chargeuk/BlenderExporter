"""Pixel ownership from final UV triangles intersected with actual bake coverage."""
import numpy as np
from scipy.ndimage import distance_transform_edt


def rasterize(raw, catalogue):
    size = raw.shape[0]
    if raw.shape != (size, size, 4) or not np.isfinite(raw).all(): raise ValueError('Invalid raw bake')
    if not np.isin(raw[:, :, 3], [0, 1]).all(): raise ValueError('Raw bake needs binary coverage alpha')
    valid = raw[:, :, 3] == 1
    if not valid.any(): raise ValueError('Bake has no owned pixels')
    labels = np.zeros((size, size), np.int32)
    conflicts = 0
    for conservative in (False, True):
        for island in catalogue:
            k = island['id']
            for tri in island['triangles']:
                v = np.array([(u * size, (1 - w) * size) for u, w in tri])
                if abs(np.linalg.det(np.stack((v[1]-v[0], v[2]-v[0])))) < 1e-12: continue
                lo = np.maximum(np.floor(v.min(0)).astype(int), 0)
                hi = np.minimum(np.ceil(v.max(0)).astype(int), size)
                x0, y0 = lo; x1, y1 = hi
                if x1 <= x0 or y1 <= y0: continue
                yy, xx = np.mgrid[y0:y1, x0:x1]; xx = xx + .5; yy = yy + .5
                hit = np.ones(xx.shape, bool)
                for i in range(3):
                    edge = v[(i+1) % 3] - v[i]; nx, ny = -edge[1], edge[0]
                    projection = v[:, 0]*nx + v[:, 1]*ny
                    q = xx*nx + yy*ny
                    radius = .5*(abs(nx)+abs(ny)) if conservative else 0
                    hit &= (q+radius >= projection.min()-1e-7) & (q-radius <= projection.max()+1e-7)
                region = labels[y0:y1, x0:x1]
                hit &= valid[y0:y1, x0:x1]
                if not conservative: conflicts += int((hit & (region > 0) & (region != k)).sum())
                region[hit & (region == 0)] = k
    missing = int((valid & (labels == 0)).sum())
    # Never invent island identity from a neighbouring chart.
    if conflicts or missing:
        raise ValueError(f'UV ownership conflicts={conflicts}, unassigned baked pixels={missing}; inspect UVs/coverage')
    present = set(map(int, np.unique(labels[valid])))
    return labels, {'owned_pixels': int(valid.sum()), 'sampled_islands': len(present),
                    'unsampled_island_ids': [i['id'] for i in catalogue if i['id'] not in present],
                    'center_conflicts': conflicts, 'unassigned_pixels': missing}


def dilate(array, labels):
    valid = labels > 0
    if not valid.any(): raise ValueError('Empty ownership')
    indices = distance_transform_edt(~valid, return_distances=False, return_indices=True)
    result = array[tuple(indices)].copy()
    if result.ndim == 3 and result.shape[2] == 4: result[:, :, 3] = 1
    if not np.array_equal(result[valid], array[valid]): raise ValueError('Dilation changed owned pixels')
    return result
