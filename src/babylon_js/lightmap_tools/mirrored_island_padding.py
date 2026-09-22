"""Tested per-island reflection indices. Input: nonempty 2D bool mask.
Returns (source_y, source_x, counts); no denoising, file writes or Blender edits.
See LIGHTMAP_PROCESSING.md beside this module for the processing contract.
"""
import numpy as np
from scipy.ndimage import distance_transform_edt

def mirrored_indices(mask):
 # Reflect about nearest owned pixel centre. Repeat if a thin/concave island is crossed.
 h,w=mask.shape;yy,xx=np.indices(mask.shape);near=distance_transform_edt(~mask,return_distances=False,return_indices=True)
 sy=yy.copy();sx=xx.copy();pending=~mask;total=int(pending.sum());first=0;folded=0
 for step in range(32):
  if not pending.any():break
  iy,ix=np.where(pending);py=sy[iy,ix];px=sx[iy,ix]
  cy=np.clip(py,0,h-1);cx=np.clip(px,0,w-1);qy=near[0,cy,cx];qx=near[1,cy,cx]
  ry=2*qy-py;rx=2*qx-px;sy[iy,ix]=ry;sx[iy,ix]=rx
  hit=(ry>=0)&(ry<h)&(rx>=0)&(rx<w)
  hit[hit]=mask[ry[hit],rx[hit]]
  pending[iy[hit],ix[hit]]=False
  if step==0:first=int(hit.sum())
  else:folded+=int(hit.sum())
 fallback=int(pending.sum())
 if fallback:
  # Degenerate one-pixel shapes or reflection cycles: nearest original same-island pixel.
  sy[pending]=near[0][pending];sx[pending]=near[1][pending]
 assert np.all(mask[sy,sx]) and np.array_equal(sy[mask],yy[mask]) and np.array_equal(sx[mask],xx[mask])
 return sy,sx,{'padding_pixels':total,'first_reflection_pixels':first,'folded_reflection_pixels':folded,'nearest_fallback_pixels':fallback}
