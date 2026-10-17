"""Pixel ownership from final UV triangles intersected with actual bake coverage."""
import numpy as np
from scipy.ndimage import distance_transform_edt


def _triangle_region(tri,size,conservative):
    v=np.array([(u*size,(1-w)*size) for u,w in tri])
    if abs(np.linalg.det(np.stack((v[1]-v[0],v[2]-v[0]))))<1e-12:return None
    lo=np.maximum(np.floor(v.min(0)).astype(int),0)
    hi=np.minimum(np.ceil(v.max(0)).astype(int),size)
    x0,y0=lo;x1,y1=hi
    if x1<=x0 or y1<=y0:return None
    yy,xx=np.mgrid[y0:y1,x0:x1];xx=xx+.5;yy=yy+.5
    hit=np.ones(xx.shape,bool)
    for i in range(3):
        edge=v[(i+1)%3]-v[i];nx,ny=-edge[1],edge[0]
        projection=v[:,0]*nx+v[:,1]*ny;q=xx*nx+yy*ny
        radius=.5*(abs(nx)+abs(ny)) if conservative else 0
        hit&=(q+radius>=projection.min()-1e-7)&(q-radius<=projection.max()+1e-7)
    return (slice(y0,y1),slice(x0,x1)),hit


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
                result=_triangle_region(tri,size,conservative)
                if result is None:continue
                slices,hit=result
                region = labels[slices]
                hit &= valid[slices]
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


def candidate_diagnostic(native_labels,catalogue,max_examples=20):
    """Conservative footprint ambiguity is sampling evidence, not geometric UV overlap."""
    size=native_labels.shape[0];valid=native_labels>0
    records=[]
    for conservative in (False,True):
        first=np.zeros(native_labels.shape,np.int32);ambiguous=np.zeros(native_labels.shape,bool)
        for island in catalogue:
            for tri in island['triangles']:
                result=_triangle_region(tri,size,conservative)
                if result is None:continue
                slices,hit=result;hit&=valid[slices];region=first[slices]
                ambiguous[slices]|=hit&(region>0)&(region!=island['id'])
                region[hit&(region==0)]=island['id']
        records.append((first,ambiguous))
    center,center_multi=records[0];conservative,conservative_multi=records[1]
    guessed=np.where(center>0,center,conservative)
    differs=valid&(guessed!=native_labels)
    return {'kind':'conservative-candidate-diagnostic','native_owned_pixels':int(valid.sum()),
        'center_candidate_ambiguous_pixels':int(center_multi.sum()),
        'conservative_candidate_ambiguous_pixels':int(conservative_multi.sum()),
        'no_center_candidate_pixels':int((valid&(center==0)).sum()),
        'no_conservative_candidate_pixels':int((valid&(conservative==0)).sum()),
        'legacy_first_candidate_differs_from_native_pixels':int(differs.sum()),
        'examples':[{'y':int(y),'x':int(x),'native_id':int(native_labels[y,x]),
                     'first_center_candidate':int(center[y,x]),'first_conservative_candidate':int(conservative[y,x]),
                     'conservative_ambiguous':bool(conservative_multi[y,x])}
                    for y,x in np.argwhere(differs|conservative_multi)[:max_examples]],
        'limits':'UV center/footprint candidates only. Multiple conservative candidates are not positive-area UV overlap. No edits, coverage filling or native owner replacement.'}


def dilate(array, labels):
    valid = labels > 0
    if not valid.any(): raise ValueError('Empty ownership')
    indices = distance_transform_edt(~valid, return_distances=False, return_indices=True)
    result = array[tuple(indices)].copy()
    if result.ndim == 3 and result.shape[2] == 4: result[:, :, 3] = 1
    if not np.array_equal(result[valid], array[valid]): raise ValueError('Dilation changed owned pixels')
    return result
