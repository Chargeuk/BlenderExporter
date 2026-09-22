"""Process decoded, zero-margin scene-linear RGBA + UV island labels.

Creates a fresh candidate directory; never edits Blender or promotes acceptance.
Supported entry point: cli.py process. See LIGHTMAP_PROCESSING.md beside it.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import numpy as np
from scipy.ndimage import distance_transform_edt, find_objects
from mirrored_island_padding import mirrored_indices
from oidn_rt import OIDN

VERSION = '1.0'


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def validate(raw, labels, catalogue=None):
    if raw.dtype != np.float32 or raw.ndim != 3 or raw.shape[2] != 4:
        raise ValueError('Raw must be float32 H x W x 4 scene-linear RGBA')
    h, w = raw.shape[:2]
    if not h or h != w:
        raise ValueError('KaDshow lightmap must be nonempty and square')
    if not np.isfinite(raw).all() or not np.isin(raw[:, :, 3], [0, 1]).all():
        raise ValueError('Raw must be finite with binary original-coverage alpha')
    if labels.shape != (h, w) or labels.dtype.kind not in 'iu' or (labels < 0).any():
        raise ValueError('Labels must be matching nonnegative integer island IDs')
    valid = raw[:, :, 3] == 1
    if not valid.any() or not np.array_equal(labels > 0, valid):
        raise ValueError('Island ownership must exactly match nonempty original coverage')
    ids = np.unique(labels[valid])
    if catalogue is not None:
        keys = [x['id'] for x in catalogue]
        if any(type(k) is not int or k <= 0 for k in keys) or len(set(keys)) != len(keys):
            raise ValueError('Catalogue must contain unique positive integer IDs')
        if not set(ids).issubset(keys):
            raise ValueError('Owned label absent from geometric island catalogue')
    return valid, ids


def process(raw, labels, denoiser, context=8):
    valid, ids = validate(raw, labels)
    h, w = labels.shape
    # Dense temporary IDs avoid a huge find_objects list for sparse source IDs.
    dense = np.zeros(labels.shape, np.int32)
    dense[valid] = np.searchsorted(ids, labels[valid]) + 1
    boxes = find_objects(dense)
    interior = np.zeros_like(raw)
    records = []
    for k, box in zip(ids, boxes):
        y0, y1 = max(0, box[0].start-context), min(h, box[0].stop+context)
        x0, x1 = max(0, box[1].start-context), min(w, box[1].stop+context)
        mask = labels[y0:y1, x0:x1] == k
        local = raw[y0:y1, x0:x1, :3]
        sy, sx, stats = mirrored_indices(mask)
        inp = local[sy, sx].copy()
        if not np.array_equal(inp[mask], local[mask]) or not mask[sy, sx].all():
            raise RuntimeError('Reflection ownership/input preservation failed')
        output = denoiser.run(inp)
        dst = interior[y0:y1, x0:x1]
        dst[mask, :3], dst[mask, 3] = output[mask], 1
        records.append({'id': int(k), 'crop': [x0, y0, x1, y1],
                        'owned_pixels': int(mask.sum()), **stats})
    nearest = distance_transform_edt(~valid, return_distances=False, return_indices=True)
    final = interior[nearest[0], nearest[1]].copy()
    final[:, :, 3] = 1
    if not np.array_equal(interior[:, :, 3] == 1, valid):
        raise RuntimeError('Copy-back changed coverage')
    if not np.array_equal(final[valid], interior[valid]):
        raise RuntimeError('Dilation changed cleaned interiors')
    return interior, final, records


def diagnostics(output, labels, records, catalogue):
    from PIL import Image, ImageDraw
    h, w = labels.shape
    rgb = np.zeros((h, w, 3), np.uint8)
    valid = labels > 0
    ids = labels[valid].astype(np.uint64)
    for channel, factor in enumerate([73, 151, 199]):
        rgb[:, :, channel][valid] = 40 + ((ids * factor) % 180).astype(np.uint8)
    edge = np.zeros_like(valid)
    edge[1:] |= labels[1:] != labels[:-1]
    edge[:-1] |= labels[:-1] != labels[1:]
    edge[:, 1:] |= labels[:, 1:] != labels[:, :-1]
    edge[:, :-1] |= labels[:, :-1] != labels[:, 1:]
    edge[[0, -1], :] |= valid[[0, -1], :]
    edge[:, [0, -1]] |= valid[:, [0, -1]]
    rgb[edge & valid] = 255
    Image.fromarray(rgb).save(output / 'island_boundaries.png')
    rgb[:] = 0
    rgb[valid] = [40, 110, 60]
    affected = [r['id'] for r in records if r['nearest_fallback_pixels']]
    rgb[np.isin(labels, affected) & valid] = [240, 165, 40]
    Image.fromarray(rgb).save(output / 'fallback_affected_islands.png')
    sampled = {r['id'] for r in records}
    if catalogue is not None:
        image = Image.fromarray(np.repeat((valid.astype(np.uint8)*65)[:, :, None], 3, 2))
        draw = ImageDraw.Draw(image)
        for island in catalogue:
            if island['id'] not in sampled:
                for triangle in island.get('triangles', []):
                    xy = [(round(float(u)*w-.5), round((1-float(v))*h-.5)) for u, v in triangle]
                    draw.line(xy+[xy[0]], fill=(255, 0, 200), width=1)
        image.save(output / 'unsampled_uv_footprints.png')
    (output / 'DIAGNOSTICS.md').write_text(
        '# Diagnostic legend\n\n'
        '- `island_boundaries.png`: colours identify sampled islands; white marks pixel boundaries; black is unowned atlas space.\n'
        '- `fallback_affected_islands.png`: amber marks whole sampled islands whose temporary context needed any nearest fallback; green marks those without fallback. These are not the positions of fallback pixels: that temporary context is discarded. Read per-island counts in `report.json` for magnitude.\n'
        '- `unsampled_uv_footprints.png` (with catalogue): magenta outlines geometric UV triangles with no owned samples, over grey sampled coverage. These are approximate raster outlines, not recovered lighting or a bake coverage mask.\n\n'
        'All images use top-left orientation. These maps diagnose ownership and processing; they do not prove applied visual quality.\n', encoding='utf-8')


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--raw', type=Path, required=True)
    p.add_argument('--labels', type=Path, required=True)
    p.add_argument('--catalogue', type=Path)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--oidn-library', type=Path, required=True)
    p.add_argument('--image-python', type=Path, help='Optional NumPy/OpenImageIO interpreter for EXRs')
    p.add_argument('--context', type=int, default=8)
    p.add_argument('--confirm-linear-zero-margin', action='store_true', required=True,
                   help='Confirm decoded linear Rec.709, top-left, original alpha, matching UV labels')
    p.add_argument('--diagnostics', action='store_true')
    args = p.parse_args(argv)
    if args.context < 0:
        p.error('Context cannot be negative')
    if args.output.exists():
        p.error('Output must be a new directory; existing results are preserved')
    inputs = [args.raw, args.labels] + ([args.catalogue] if args.catalogue else [])
    before = {str(x.resolve(strict=True)): digest(x) for x in inputs}
    raw = np.load(args.raw, allow_pickle=False)
    labels = np.load(args.labels, allow_pickle=False)
    catalogue = json.loads(args.catalogue.read_text(encoding='utf-8')) if args.catalogue else None
    valid, ids = validate(raw, labels, catalogue)
    library = args.oidn_library.resolve(strict=True)
    if args.image_python:
        args.image_python.resolve(strict=True)
    args.output.mkdir(parents=True, exist_ok=False)
    from cli import addon_version
    report = {'tool_version': VERSION, 'plugin_version': addon_version(),
              'status': 'processing', 'accepted': False,
              'inputs_sha256': before, 'command': sys.argv,
              'input_contract': 'decoded linear Rec.709; top-left RGBA; zero-margin binary alpha; corresponding UV labels',
              'context_pixels': args.context, 'reflection_iterations': 32,
              'filter': 'RT', 'hdr': True, 'quality': 'HIGH', 'input_scale': 1.0,
              'oidn_library': str(library), 'oidn_library_sha256': digest(library),
              'script_sha256': {f.name: digest(f) for f in Path(__file__).parent.glob('*.py')},
              'dimensions': list(labels.shape), 'owned_pixels': int(valid.sum()),
              'sampled_islands': len(ids),
              'unsampled_island_ids': sorted({x['id'] for x in catalogue} - set(map(int, ids))) if catalogue else None}
    started = time.time()
    try:
        with OIDN(library, labels.size) as denoiser:
            report.update(oidn_version=denoiser.version, oidn_device_type=denoiser.device_type)
            interiors, final, records = process(raw, labels, denoiser, args.context)
        np.save(args.output / 'interiors.npy', interiors)
        np.save(args.output / 'final.npy', final)
        report['islands'] = records
        report['totals'] = {key: sum(r[key] for r in records) for key in
                            ['padding_pixels', 'first_reflection_pixels', 'folded_reflection_pixels', 'nearest_fallback_pixels']}
        if args.image_python:
            subprocess.run([str(args.image_python), str(Path(__file__).with_name('write_linear_exr.py')),
                            str(args.output / 'interiors.npy'), str(args.output / 'final.npy')], check=True)
            report['exr_roundtrip_exact'] = True
        if args.diagnostics:
            diagnostics(args.output, labels, records, catalogue)
        after = {path: digest(path) for path in before}
        if before != after:
            raise RuntimeError('Processing inputs changed during execution')
        report.update(status='candidate_processing_verified', seconds=time.time()-started,
                      input_files_unchanged=True, cleaned_interiors_preserved=True,
                      output_sha256={f.name: digest(f) for f in args.output.iterdir() if f.is_file()})
    except Exception as error:
        report.update(status='failed', error=repr(error), seconds=time.time()-started)
        raise
    finally:
        (args.output / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k in
                      ['status', 'sampled_islands', 'owned_pixels', 'seconds', 'totals']}))


if __name__ == '__main__':
    main()
