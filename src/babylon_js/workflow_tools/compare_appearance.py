"""Compare aligned sRGB screenshots; diagnostic appearance, never recovered albedo.

Standalone CPU tool: Python + NumPy + Pillow + SciPy. See the kadshow-environment-refinement Codex skill.
"""
import argparse
import hashlib
import html
import io
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageCms, ImageDraw
from scipy.ndimage import binary_erosion, gaussian_filter


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_rgb(path, assume_srgb=False):
    with Image.open(path) as im:
        if im.format != 'PNG' or im.mode not in ('RGB', 'RGBA'):
            raise ValueError('Use 8-bit RGB/RGBA PNG screenshots: ' + str(path))
        if im.mode == 'RGBA' and np.asarray(im)[:, :, 3].min() != 255:
            raise ValueError('Screenshot transparency is unsupported; render an opaque background')
        info = dict(im.info)
        rgb = im.convert('RGB')
        if info.get('icc_profile'):
            rgb = ImageCms.profileToProfile(rgb, ImageCms.ImageCmsProfile(io.BytesIO(info['icc_profile'])),
                                           ImageCms.createProfile('sRGB'), outputMode='RGB')
            interpretation = 'embedded ICC converted to sRGB'
        elif 'srgb' in info:
            interpretation = 'PNG sRGB tag'
        elif assume_srgb:
            interpretation = 'explicit --assume-srgb; no usable sRGB/ICC tag'
        else:
            raise ValueError('No sRGB/ICC tag; verify screenshot interpretation then use --assume-srgb')
        return np.asarray(rgb, dtype=np.float64) / 255, {
            'path': str(Path(path).resolve()), 'sha256': sha(path),
            'interpretation': interpretation, 'gamma_tag': info.get('gamma')}


def decode(rgb):
    return np.where(rgb <= .04045, rgb / 12.92, ((rgb + .055) / 1.055) ** 2.4)


def lab(rgb):
    linear = decode(rgb)
    xyz = linear @ np.array([[.4124564, .3575761, .1804375],
                              [.2126729, .7151522, .0721750],
                              [.0193339, .1191920, .9503041]]).T
    t = xyz / np.array([.95047, 1, 1.08883])
    d = 6 / 29
    f = np.where(t > d**3, np.cbrt(t), t / (3*d*d) + 4/29)
    return np.stack([116*f[..., 1]-16, 500*(f[..., 0]-f[..., 1]),
                     200*(f[..., 1]-f[..., 2])], axis=-1)


def components(candidate, target, neutral_chroma=5):
    a, b = lab(candidate), lab(target)
    ca, cb = np.linalg.norm(a[..., 1:], axis=-1), np.linalg.norm(b[..., 1:], axis=-1)
    da = np.arctan2(a[..., 2], a[..., 1]) - np.arctan2(b[..., 2], b[..., 1])
    angle = np.arctan2(np.sin(da), np.cos(da))
    weight = np.clip(np.minimum(ca, cb) / neutral_chroma, 0, 1)
    hue = 2*np.sqrt(ca*cb)*np.abs(np.sin(angle/2))*weight
    return {'lightness': a[..., 0]-b[..., 0], 'chroma': ca-cb,
            'hue': hue, 'hue_degrees': np.degrees(angle), 'hue_weight': weight,
            'a': a[..., 1]-b[..., 1], 'b': a[..., 2]-b[..., 2],
            'delta_e76': np.linalg.norm(a-b, axis=-1),
            'candidate_lab': a, 'target_lab': b}


def polygon_mask(points, size):
    pts = np.asarray(points, dtype=float)
    if pts.ndim != 2 or pts.shape[1] != 2 or len(pts) < 3 or not np.isfinite(pts).all():
        raise ValueError('Each polygon requires at least three finite [x,y] points')
    w, h = size
    if (pts < 0).any() or (pts[:, 0] >= w).any() or (pts[:, 1] >= h).any():
        raise ValueError('Polygon outside image dimensions; do not silently rescale masks')
    canvas = Image.new('L', size)
    ImageDraw.Draw(canvas).polygon([tuple(p) for p in pts], fill=255)
    return np.asarray(canvas) > 0


def masks(config, size, erosion):
    if config.get('size') != list(size):
        raise ValueError('Region JSON size must equal screenshot [width,height]')
    excluded = np.zeros(size[::-1], bool)
    for row in config.get('exclude', []):
        if not row.get('reason'):
            raise ValueError('Every exclusion needs a reason')
        excluded |= polygon_mask(row['polygon'], size)
    valid = ~excluded
    rows, names = [], set()
    for row in config.get('regions', []):
        if not row.get('name') or row['name'] in names or not row.get('family'):
            raise ValueError('Regions need unique names and material-family labels')
        names.add(row['name'])
        mask = polygon_mask(row['polygon'], size) & valid
        if erosion:
            mask = binary_erosion(mask, iterations=erosion)
        if not mask.any():
            raise ValueError('Empty region after exclusions/erosion: '+row['name'])
        rows.append((row, mask))
    if not valid.any():
        raise ValueError('No valid pixels')
    return valid, rows


def blur_masked(values, valid, sigma):
    # Excluded reflection/window pixels must not bleed into nearby diffuse diagnostics.
    if sigma == 0:
        return values.copy()
    numerator = gaussian_filter(values*valid, sigma=sigma)
    denominator = gaussian_filter(valid.astype(float), sigma=sigma)
    return np.divide(numerator, denominator, out=np.zeros_like(values), where=denominator > 1e-12)


def save_image(path, rgb):
    Image.fromarray(np.rint(np.clip(rgb, 0, 1)*255).astype('uint8'), 'RGB').save(path)


def signed_image(values, scale, valid):
    a = np.clip(values / scale, -1, 1)
    result = np.stack([np.maximum(a, 0), np.zeros_like(a), np.maximum(-a, 0)], axis=-1)
    result[~valid] = .22
    return result


def stats(comp, cy, ty, blurred, mask):
    def p(v):
        return [float(x) for x in np.percentile(v[mask], [10, 50, 90])]
    hue_valid = mask & (comp['hue_weight'] >= 1)
    return {'pixels': int(mask.sum()), 'candidate_Y_p10_p50_p90': p(cy),
            'target_Y_p10_p50_p90': p(ty),
            'candidate_chroma_p10_p50_p90': p(np.linalg.norm(comp['candidate_lab'][...,1:],axis=-1)),
            'target_chroma_p10_p50_p90': p(np.linalg.norm(comp['target_lab'][...,1:],axis=-1)),
            'signed_lightness_median': float(np.median(comp['lightness'][mask])),
            'signed_chroma_median': float(np.median(comp['chroma'][mask])),
            'signed_a_median': float(np.median(comp['a'][mask])),
            'signed_b_median': float(np.median(comp['b'][mask])),
            'hue_component_mean': float(np.mean(comp['hue'][mask])),
            'reliable_hue_pixels': int(hue_valid.sum()),
            'absolute_hue_degrees_median': float(np.median(np.abs(comp['hue_degrees'][hue_valid]))) if hue_valid.any() else None,
            'delta_e76_mean': float(np.mean(comp['delta_e76'][mask])),
            'delta_e76_p90': float(np.percentile(comp['delta_e76'][mask], 90)),
            'blurred_Y_mae': float(np.mean(np.abs(blurred[mask]))),
            'candidate_black_or_white_fraction': float(np.mean((cy[mask] <= 0) | (cy[mask] >= 1-1e-6)))}


def compare(candidate, target, valid, regions, output, scales, sigma, neutral):
    output.mkdir()
    comp = components(candidate, target, neutral)
    cy, ty = decode(candidate) @ [.2126, .7152, .0722], decode(target) @ [.2126, .7152, .0722]
    blurred = blur_masked(cy, valid, sigma)-blur_masked(ty, valid, sigma)
    rgb = np.stack([np.abs(comp['lightness'])/scales[0], np.abs(comp['chroma'])/scales[1],
                    comp['hue']/scales[2]], axis=-1)
    rgb = np.clip(rgb, 0, 1)
    rgb[~valid] = .22
    save_image(output/'difference_rgb.png', rgb)
    save_image(output/'overlay.png', candidate*.5+rgb*.5)
    for i, key in enumerate(('lightness', 'chroma', 'hue')):
        v = np.clip(np.abs(comp[key])/scales[i], 0, 1)
        im = np.repeat(v[..., None], 3, axis=-1); im[~valid] = .22
        save_image(output/(key+'_magnitude.png'), im)
    for key, scale in [('lightness',scales[0]), ('chroma',scales[1]), ('a',scales[2]), ('b',scales[2])]:
        save_image(output/(key+'_signed.png'), signed_image(comp[key], scale, valid))
    save_image(output/'blurred_luminance_signed.png', signed_image(blurred, scales[3], valid))
    Image.fromarray(valid.astype('uint8')*255).save(output/'valid_mask.png')
    np.savez_compressed(output/'differences.npz', **{k:v.astype('float32') for k,v in comp.items()
                                                  if not k.endswith('_lab')}, blurred_Y=blurred.astype('float32'), valid=valid)
    labelled = Image.fromarray(np.rint(candidate*255).astype('uint8'))
    draw = ImageDraw.Draw(labelled)
    results = []
    for index, (row, mask) in enumerate(regions, 1):
        y,x = np.where(mask)
        bounds = (int(x.min()),int(y.min()),int(x.max())+1,int(y.max())+1)
        draw.rectangle(bounds, outline='magenta', width=2)
        draw.text((bounds[0]+2,bounds[1]+2), str(index)+': '+row['name'], fill='white', stroke_width=1, stroke_fill='black')
        a,b,c,d = bounds
        crop = np.concatenate([candidate[b:d,a:c], target[b:d,a:c], rgb[b:d,a:c]], axis=1)
        save_image(output/f'region_{index:02d}.png', crop)
        results.append({'number':index,'name':row['name'],'family':row['family'],
                        'crop_order':['candidate','target','diagnostic'], **stats(comp,cy,ty,blurred,mask)})
    labelled.save(output/'regions.png')
    return {'global':stats(comp,cy,ty,blurred,valid), 'regions':results}


def run(args):
    scales = [args.lightness_scale,args.chroma_scale,args.hue_scale,args.luminance_scale]
    if not all(np.isfinite(v) and v > 0 for v in scales+[args.neutral_chroma]) or not np.isfinite(args.blur_sigma) or args.blur_sigma < 0 or args.erode < 0:
        raise ValueError('Positive finite scales/chroma threshold and nonnegative blur/erosion required')
    target, ti = read_rgb(args.target,args.assume_srgb)
    candidate, ci = read_rgb(args.candidate,args.assume_srgb)
    if candidate.shape != target.shape:
        raise ValueError('Image dimensions differ; establish aligned screenshots first')
    w,h = target.shape[1],target.shape[0]
    cfg = json.loads(Path(args.regions).read_text(encoding='utf-8-sig')) if args.regions else {'size':[w,h]}
    valid, regions = masks(cfg,(w,h),args.erode)
    baseline, bi = read_rgb(args.baseline,args.assume_srgb) if args.baseline else (None,None)
    if baseline is not None and baseline.shape != target.shape:
        raise ValueError('Baseline dimensions differ')
    out = Path(args.output)
    if out.exists():
        raise ValueError('Choose a new output directory; existing results are preserved')
    out.mkdir(parents=True)
    report = {'schema_version':1, 'tool_sha256':sha(__file__), 'inputs':{'candidate':ci,'target':ti,'baseline':bi},
              'settings':{'scales_L_C_H_Y':scales,'neutral_chroma':args.neutral_chroma,'blur_sigma_pixels':args.blur_sigma,
                          'erosion_pixels':args.erode, 'rgb_channels':['absolute Lab L*','absolute Lab C*','neutral-weighted Lab hue component'],
                          'signed_convention':'candidate minus target; red positive, blue negative, black zero; excluded gray',
                          'colour_space':'sRGB decoded once; CIELAB D65; delta E 1976 (not delta E 2000)',
                          'limitations':'Appearance only; no albedo/lighting separation, reflection detection or automatic correction'},
              'region_config':cfg,'regions_sha256':sha(args.regions) if args.regions else None}
    report['candidate'] = compare(candidate,target,valid,regions,out/'candidate',scales,args.blur_sigma,args.neutral_chroma)
    if baseline is not None:
        report['baseline'] = compare(baseline,target,valid,regions,out/'baseline',scales,args.blur_sigma,args.neutral_chroma)
    (out/'report.json').write_text(json.dumps(report,indent=2,allow_nan=False),encoding='utf-8')
    headings = '<p>RGB: red brightness, green chroma, blue hue mismatch. Black = close; gray = excluded. Fixed scales; no causal separation. Region crops: candidate | target | diagnostic. See report.json for signed values and baseline comparison.</p>'
    sections = []
    for group in ('candidate','baseline'):
        if group not in report: continue
        sections.append(f'<h2>{group}</h2><img src="{group}/difference_rgb.png"><img src="{group}/regions.png"><p>Blurred luminance: red too bright; blue too dark; gray excluded.</p><img src="{group}/blurred_luminance_signed.png">')
        sections.append('<table><tr><th>Region / family</th><th>Median Y actual / target</th><th>Median chroma actual / target</th><th>Signed L / C / a / b</th><th>Mean Delta E76</th><th>Blur Y MAE</th></tr>')
        for r in report[group]['regions']:
            label=html.escape(r['name']+' / '+r['family'])
            y=f"{r['candidate_Y_p10_p50_p90'][1]:.3f} / {r['target_Y_p10_p50_p90'][1]:.3f}"
            c=f"{r['candidate_chroma_p10_p50_p90'][1]:.2f} / {r['target_chroma_p10_p50_p90'][1]:.2f}"
            signed=' / '.join(f"{r['signed_'+key+'_median']:+.2f}" for key in ('lightness','chroma','a','b'))
            sections.append(f"<tr><td>{label}</td><td>{y}</td><td>{c}</td><td>{signed}</td><td>{r['delta_e76_mean']:.2f}</td><td>{r['blurred_Y_mae']:.4f}</td></tr>")
        sections.append('</table>')
        sections.extend(f'<p>Region {r["number"]}</p><img src="{group}/region_{r["number"]:02d}.png">' for r in report[group]['regions'])
    (out/'index.html').write_text('<!doctype html><meta charset="utf-8"><title>Appearance differences</title><style>body{background:#222;color:#eee;font:16px sans-serif}img{max-width:100%;display:block;margin-bottom:20px}td,th{padding:8px;border:1px solid #777}table{border-collapse:collapse}</style><h1>Appearance differences</h1>'+headings+''.join(sections),encoding='utf-8')
    return report


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('candidate','target','output'):
        p.add_argument('--'+name,required=True)
    p.add_argument('--baseline'); p.add_argument('--regions')
    p.add_argument('--assume-srgb',action='store_true')
    for name,value in [('lightness-scale',20.),('chroma-scale',20.),('hue-scale',20.),('luminance-scale',.2),('neutral-chroma',5.),('blur-sigma',12.)]:
        p.add_argument('--'+name,type=float,default=value)
    p.add_argument('--erode',type=int,default=3)
    return p


if __name__ == '__main__':
    try:
        result = run(parser().parse_args())
        print(json.dumps({'status':'passed','regions':len(result['candidate']['regions']),
                          'mean_delta_e76':result['candidate']['global']['delta_e76_mean']}))
    except (ValueError, OSError, KeyError) as exc:
        raise SystemExit(str(exc))
