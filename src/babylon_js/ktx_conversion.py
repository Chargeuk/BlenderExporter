"""Prepare PNGs, convert to KTX2, and update one or more Babylon delivery copies.

Run --plan to preview a JSON configuration without writing files. Run --inspect
scene.babylon to list texture references and KaDshow lightmap markers.
"""
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def normalized(reference):
    # Deliberately do not collapse to basename, case-fold URLs or URL-decode.
    return reference.replace('\\', '/')


def output_name(value, extension):
    if not isinstance(value, str) or not value or '/' in value or '\\' in value or ':' in value:
        raise ValueError(f'Output must be a plain filename: {value!r}')
    if not value.lower().endswith(extension) or value in ('.', '..'):
        raise ValueError(f'Output must end in {extension}: {value!r}')
    return value


def lightmap_output_name(value):
    """KaDshow strips every lightmap_ token; keep one token in the marker only."""
    value = output_name(value, '.ktx2')
    stem = Path(value).stem.replace('lightmap_', '')
    if not stem:
        raise ValueError('Lightmap output needs a nonempty stem without lightmap_')
    return stem + '.ktx2'


def lightmap_size(value):
    if type(value) is not int or value not in (0, 512, 1024, 2048):
        raise ValueError('Lightmap size must be 0 (original), 512, 1024 or 2048')
    return value


def resize_lightmap(array, spec, hdr):
    """Area-average linear light before RGBD/gamma encoding; never upscale."""
    import numpy as np
    target = lightmap_size(spec.get('size', 0))
    h, w = array.shape[:2]
    if not target:
        return array
    if h != w:
        raise ValueError('A fixed lightmap size requires a square source')
    if target >= w:
        return array
    result = np.asarray(array, dtype=np.float32).copy()
    srgb = not hdr and spec['color_space'] == 'srgb'
    if srgb:
        rgb = result[..., :3]
        result[..., :3] = np.where(rgb <= .04045, rgb / 12.92, ((rgb + .055) / 1.055) ** 2.4)
    for axis in (0, 1):
        src = np.moveaxis(result, axis, 0)
        length = len(src)
        if length % target == 0:
            dst = src.reshape(target, length // target, *src.shape[1:]).mean(axis=1)
        else:
            # Exact box integration for non-power-of-two square sources.
            cumulative = np.concatenate((np.zeros_like(src[:1], dtype=np.float64), np.cumsum(src, axis=0, dtype=np.float64)))
            edges = np.linspace(0, length, target + 1)
            ix = np.minimum(edges.astype(int), length - 1)
            f = (edges - ix).reshape((-1,) + (1,) * (src.ndim - 1))
            integral = cumulative[ix] + f * src[ix]
            dst = np.diff(integral, axis=0) / (length / target)
        result = np.moveaxis(dst.astype(np.float32), 0, axis)
    if srgb:
        rgb = result[..., :3]
        result[..., :3] = np.where(rgb <= .0031308, rgb * 12.92, 1.055 * np.maximum(rgb, 0) ** (1 / 2.4) - .055)
    return result


def encode_rgbd(rgb, *, fixed_divisor=False):
    """rgbd-v1: Babylon 7.27 default gamma2.2 RGBD in RGBA8 UNORM."""
    import numpy as np
    rgb = np.asarray(rgb, dtype=np.float32)
    if not np.isfinite(rgb).all() or np.any(rgb < 0) or np.any(rgb > 255):
        raise ValueError('RGBD requires finite nonnegative linear RGB in [0,255]; source is not clipped')
    peak = np.maximum(rgb.max(axis=-1), 1e-7)
    if fixed_divisor: peak = np.full_like(peak, peak.max())
    divisor = np.minimum(np.floor(np.maximum(255.0 / peak, 1.0)), 255.0) / 255.0
    encoded = np.concatenate((np.power(np.clip(rgb * divisor[..., None], 0, 1), 1 / 2.2), divisor[..., None]), axis=-1)
    pixels = np.rint(encoded * 255).astype(np.uint8)
    restored = np.power(pixels[..., :3].astype(np.float32) / 255, 2.2) / (pixels[..., 3:4].astype(np.float32) / 255)
    error = np.abs(restored - rgb)
    return pixels, dict(linear_min=float(rgb.min()), linear_max=float(rgb.max()),
        alpha_min=int(pixels[..., 3].min()), alpha_max=int(pixels[..., 3].max()),
        rgbd_quantization_mae=float(error.mean()), rgbd_quantization_max_error=float(error.max()))


def texture_fields(node, location='$'):
    """Yield semantic texture reference fields, not arbitrary matching strings.

    Supports Babylon *Texture dictionaries/strings, nested material texture
    slots, and arrays under 'textures'. Does not replace mesh/material names.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            here = f'{location}.{key}'
            lower = key.lower()
            if lower.endswith('texture'):
                if isinstance(value, str):
                    yield node, key, None, here
                elif isinstance(value, dict):
                    for field in ('name', 'url'):
                        if isinstance(value.get(field), str):
                            yield value, field, value, f'{here}.{field}'
            elif lower == 'textures' and isinstance(value, list):
                for index, texture in enumerate(value):
                    if isinstance(texture, dict):
                        for field in ('name', 'url'):
                            if isinstance(texture.get(field), str):
                                yield texture, field, texture, f'{here}[{index}].{field}'
            yield from texture_fields(value, here)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from texture_fields(value, f'{location}[{index}]')


def inspect_model(model):
    return dict(texture_references=[dict(location=loc, reference=owner[key])
                for owner, key, _, loc in texture_fields(model)],
                lightmap_markers=[node['name'] for collection in ('meshes', 'transformNodes')
                for node in model.get(collection, []) if str(node.get('name', '')).startswith('lightmap_')])


def load_plan(config_path):
    config_path = config_path.resolve()
    root = config_path.parent
    config = read_json(config_path)
    if config.get('version') != 1:
        raise ValueError('Configuration version must be 1')
    models, textures = config.get('models', []), config.get('textures', [])
    if not models or not textures:
        raise ValueError('Configure at least one model and one texture')
    output_dir = (root / config.get('output_dir', '..')).resolve()
    claimed, sources, refs, markers = set(), set(), {}, {}
    specs = []
    def claim(name):
        if name.casefold() in claimed:
            raise ValueError(f'Duplicate output filename: {name}')
        claimed.add(name.casefold())
    for original in textures:
        spec = dict(original)
        spec['output'] = (lightmap_output_name(spec['output']) if spec.get('lightmap_markers')
                          else output_name(spec['output'], '.ktx2'))
        claim(spec['output'])
        source = (root / spec['source']).resolve()
        if not source.is_file():
            raise ValueError(f'Missing source texture: {source}')
        sources.add(source)
        for flag in ('flip_y', 'mipmaps'):
            if not isinstance(spec.get(flag), bool):
                raise ValueError(f'{flag} must be explicitly true/false: {spec["output"]}')
        if spec.get('color_space') not in ('srgb', 'linear'):
            raise ValueError('color_space must be srgb or linear')
        if spec.get('codec') not in ('basis-lz', 'uastc'):
            raise ValueError('codec must be basis-lz or uastc')
        spec.setdefault('alpha', 'preserve')
        if spec['alpha'] not in ('preserve', 'opaque', 'discard'):
            raise ValueError('alpha must be preserve, opaque or discard')
        spec['size'] = lightmap_size(spec.get('size', 0))
        if spec['size'] and not spec.get('lightmap_markers'):
            raise ValueError('Fixed size is supported for marker lightmaps only')
        spec.setdefault('encoding', 'legacy')
        if spec['encoding'] not in ('legacy', 'rgbd-v1'):
            raise ValueError('Unsupported lightmap encoding: ' + str(spec['encoding']))
        if spec['encoding'] == 'rgbd-v1':
            if (not spec.get('lightmap_markers') or spec.get('references') or
                source.suffix.lower() not in ('.exr', '.hdr') or spec['color_space'] != 'linear' or
                spec['alpha'] != 'preserve' or spec['mipmaps'] or spec['codec'] != 'uastc'):
                raise ValueError('RGBD requires a marker lightmap, HDR/EXR source, RGBA linear UNORM, UASTC and no mipmaps')
        if spec.get('rgbd_region_policy'):
            if spec['encoding'] != 'rgbd-v1': raise ValueError('Region policy requires rgbd-v1')
            if isinstance(spec['rgbd_region_policy'], str):
                spec['rgbd_region_policy'] = load_region_policy(root / spec['rgbd_region_policy'])
        spec['source_path'] = str(source)
        specs.append(spec)
        for field, mapping in [('references', refs), ('lightmap_markers', markers)]:
            entries = spec.get(field, [])
            if not isinstance(entries, list) or any(not isinstance(s, str) or not s for s in entries):
                raise ValueError(f'{field} must be a list of nonempty strings')
            for reference in entries:
                if field == 'lightmap_markers' and not reference.startswith('lightmap_'):
                    raise ValueError('Lightmap marker names must start with lightmap_')
                match = normalized(reference) if field == 'references' else reference
                if match in mapping:
                    raise ValueError(f'Ambiguous duplicate mapping: {reference}')
                mapping[match] = spec
    rewritten, changes = [], []
    ref_hits, marker_hits = set(), set()
    for entry in models:
        source = (root / entry['source']).resolve()
        sources.add(source)
        name = output_name(entry.get('output', source.name), '.babylon')
        claim(name)
        model = read_json(source)
        delivered = copy.deepcopy(model)
        omitted = {}
        if entry.get('omit_world_environment', False):
            omitted = {k: delivered.pop(k) for k in list(delivered) if k.startswith('environmentTexture')}
        untouched = []
        for owner, field, texture, location in texture_fields(delivered):
            reference = owner[field]
            match = normalized(reference)
            if match not in refs:
                untouched.append(dict(location=location, reference=reference))
                continue
            spec = refs[match]
            owner[field] = spec['output']
            if texture is not None:
                gamma = spec['color_space'] == 'srgb'
                if texture.get('gammaSpace') != gamma:
                    changes.append(dict(model=name, location=location.rsplit('.', 1)[0] + '.gammaSpace',
                                        before=texture.get('gammaSpace'), after=gamma))
                texture['gammaSpace'] = gamma
            ref_hits.add(match)
            changes.append(dict(model=name, location=location, before=reference, after=spec['output']))
        for collection in ('meshes', 'transformNodes'):
            for index, node in enumerate(delivered.get(collection, [])):
                name_before = node.get('name', '')
                if name_before in markers:
                    spec = markers[name_before]
                    node['name'] = 'lightmap_' + Path(spec['output']).stem
                    metadata = node.get('metadata') or {}
                    if not isinstance(metadata, dict):
                        raise ValueError('Lightmap marker metadata must be an object')
                    if spec['encoding'] == 'rgbd-v1':
                        metadata['kadshowLightmapEncoding'] = 'rgbd-v1'
                        node['metadata'] = metadata
                    elif 'kadshowLightmapEncoding' in metadata:
                        del metadata['kadshowLightmapEncoding']
                        metadata.pop('kadshowLightmapMinDivisor', None)
                        node['metadata'] = metadata
                    marker_hits.add(name_before)
                    changes.append(dict(model=name, location=f'$.{collection}[{index}].name',
                                        before=name_before, after=node['name']))
                    # Keep IDs/parent IDs: the hierarchy must not be reparented.
        rewritten.append(dict(source=str(source), source_sha256=sha(source), output=name,
                              data=delivered, omitted_world_fields=omitted, unchanged_references=untouched))
    missing = (set(refs) - ref_hits) | (set(markers) - marker_hits)
    if missing:
        raise ValueError('Configured references/markers not found: ' + ', '.join(sorted(missing)))
    outputs = [output_dir / spec['output'] for spec in specs + rewritten]
    if any(path.resolve() in sources for path in outputs):
        raise ValueError('Delivery output would overwrite a configured source file')
    return dict(config=str(config_path), root=str(root), output_dir=str(output_dir),
                textures=specs, models=rewritten, changes=changes)


def prepare(spec, directory):
    import numpy as np
    from PIL import Image
    source = Path(spec['source_path'])
    metrics = {}
    if source.suffix.lower() in ('.exr', '.hdr'):
        os.environ.setdefault('OPENCV_IO_ENABLE_OPENEXR', '1')
        import cv2
        raw = cv2.imread(str(source), cv2.IMREAD_UNCHANGED)
        if raw is None or raw.ndim != 3 or raw.shape[2] not in (3, 4):
            raise ValueError(f'Cannot decode RGB HDR image: {source}')
        source_dimensions = [raw.shape[1], raw.shape[0]]
        raw = resize_lightmap(raw, spec, True)
        rgb = raw[:, :, [2, 1, 0]].astype(np.float32)
        if not np.isfinite(rgb).all():
            raise ValueError(f'Non-finite RGB values: {source}')
        metrics = dict(linear_min=float(rgb.min()), linear_max=float(rgb.max()),
                       pixels_above_one_percent=float(np.mean(np.any(rgb > 1, axis=2)) * 100))
        if spec.get('encoding') == 'rgbd-v1':
            pixels, rgbd_metrics = encode_rgbd(rgb)
            metrics.update(rgbd_metrics)
            if spec.get('rgbd_region_policy'):
                original = pixels.copy()
                pixels, selected, region = encode_rgbd_regions(rgb, original, spec['rgbd_region_policy'])
                metrics.update(region_artifacts(original, selected, spec, directory, lambda path, value: Image.fromarray(value).save(path)), **region)
        else:
            rgb = np.clip(rgb, 0, 1)
            if spec['color_space'] == 'srgb':
                rgb = np.where(rgb <= 0.0031308, rgb * 12.92, 1.055 * np.power(rgb, 1 / 2.4) - 0.055)
            pixels = np.rint(np.clip(rgb, 0, 1) * 255).astype(np.uint8)
            if raw.shape[2] == 4 and spec['alpha'] != 'discard':
                alpha = raw[:, :, 3]
                if not np.isfinite(alpha).all():
                    raise ValueError(f'Non-finite alpha: {source}')
                if spec['alpha'] == 'opaque' and not np.all(alpha == 1):
                    raise ValueError(f'Non-opaque source: {source}')
                if spec['alpha'] == 'preserve':
                    pixels = np.dstack((pixels, np.rint(np.clip(alpha, 0, 1) * 255).astype(np.uint8)))
        image = Image.fromarray(pixels)
    else:
        with Image.open(source) as original:
            if original.mode not in ('RGB', 'RGBA', 'L', 'LA', 'P', '1'):
                raise ValueError(f'Convert unsupported PNG/input mode {original.mode} explicitly: {source}')
            image = original.convert('RGBA')
        source_dimensions = list(image.size)
        array = resize_lightmap(np.asarray(image).astype(np.float32) / 255, spec, False)
        image = Image.fromarray(np.rint(np.clip(array, 0, 1) * 255).astype(np.uint8))
        if spec['alpha'] == 'opaque' and image.getchannel('A').getextrema() != (255, 255):
            raise ValueError(f'Non-opaque source: {source}')
        if spec['alpha'] != 'preserve':
            image = image.convert('RGB')
    stem = Path(spec['output']).stem
    image.save(directory / (stem + '_unflipped.png'))
    prepared = directory / (stem + ('_flipped.png' if spec['flip_y'] else '_prepared.png'))
    transformed = image.transpose(Image.Transpose.FLIP_TOP_BOTTOM) if spec['flip_y'] else image.copy()
    transformed.save(prepared)
    with Image.open(prepared) as reopened:
        expected = np.asarray(image)[::-1] if spec['flip_y'] else np.asarray(image)
        if not np.array_equal(np.asarray(reopened), expected):
            raise RuntimeError('PNG/flip verification failed')
    return prepared, dict(source_sha256=sha(source), source_dimensions=source_dimensions, dimensions=list(image.size),
                          channels=len(image.getbands()), prepared=str(prepared),
                          prepared_sha256=sha(prepared), **metrics)


def commit_delivery(files, destination, run):
    """Back up the previous set and restore it if any copy fails."""
    destination.mkdir(parents=True, exist_ok=True)
    backup = run / 'previous_delivery'
    backup.mkdir()
    previous, attempted = {}, []
    for file in files:
        target = destination / file.name
        if target.exists():
            if not target.is_file():
                raise ValueError(f'Output is not a regular file: {target}')
            shutil.copy2(target, backup / file.name)
            previous[file.name] = backup / file.name
    try:
        for file in files:
            target = destination / file.name
            attempted.append(target)
            shutil.copy2(file, target)
            if sha(file) != sha(target):
                raise RuntimeError(f'Delivery copy verification failed: {target}')
    except Exception:
        for target in reversed(attempted):
            if target.name in previous:
                shutil.copy2(previous[target.name], target)
            elif target.is_file():
                target.unlink()
        raise


def apply_rgbd_bounds(model, textures):
    """Keep compressed alpha from decoding beyond the source encoding range."""
    for texture in textures:
        if texture.get('encoding') != 'rgbd-v1':
            continue
        marker_name = 'lightmap_' + Path(texture['output']).stem
        for collection in ('meshes', 'transformNodes'):
            for node in model.get(collection, []):
                if node.get('name') == marker_name:
                    node['metadata']['kadshowLightmapMinDivisor'] = texture['alpha_min'] / 255.0


def run_conversion(plan, ktx, threads, prepare_image=None):
    root = Path(plan['root'])
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
    run = root / 'textures/conversion_runs' / stamp
    prepared_dir, staging = run / 'prepared', run / 'delivery'
    prepared_dir.mkdir(parents=True)
    staging.mkdir()
    report = dict(status='running', run=str(run), config=plan['config'], textures=[], commands=[],
                  changes=plan['changes'], models=[{k:v for k,v in m.items() if k != 'data'} for m in plan['models']])
    (run / 'config.json').write_text(Path(plan['config']).read_text(encoding='utf-8-sig'), encoding='utf8')
    def save():
        (run / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf8')
    def call(command):
        print('RUN', subprocess.list2cmdline(command), flush=True)
        flags = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
        proc = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **flags)
        report['commands'].append(dict(argv=command, returncode=proc.returncode, output=proc.stdout))
        save()
        if proc.returncode:
            raise RuntimeError(proc.stdout)
        return proc.stdout
    try:
        report['ktx_version'] = call([ktx, '--version']).strip()
        for spec in plan['textures']:
            png, metrics = (prepare_image or prepare)(spec, prepared_dir)
            output = staging / spec['output']
            prefix = 'R8G8B8A8' if metrics['channels'] == 4 else 'R8G8B8'
            command = [ktx, 'create', '--format', prefix + ('_SRGB' if spec['color_space'] == 'srgb' else '_UNORM'),
                       '--assign-oetf', spec['color_space'], '--encode', spec['codec'], '--threads', str(threads)]
            if spec['mipmaps']:
                command += ['--generate-mipmap']
            command += ['--clevel', '5', '--qlevel', '255'] if spec['codec'] == 'basis-lz' else ['--uastc-quality', '4', '--zstd', '3']
            call(command + [str(png), str(output)])
            call([ktx, 'validate', str(output)])
            if metrics.get('rgbd_region_baseline'):
                import numpy as np
                baseline = prepared_dir / (Path(spec['output']).stem + '_baseline.ktx2')
                call(command + [metrics['rgbd_region_baseline'], str(baseline)])
                arrays = []
                for index, texture in enumerate((baseline, output)):
                    decoded = prepared_dir / (Path(spec['output']).stem + f'_decoded_{index}.raw')
                    call([ktx, 'extract', '--transcode', 'rgba8', '--raw', str(texture), str(decoded)])
                    arrays.append(np.frombuffer(decoded.read_bytes(), dtype=np.uint8).reshape(metrics['dimensions'][1], metrics['dimensions'][0], 4))
                footprint = np.load(metrics['rgbd_region_footprint'], allow_pickle=False)
                if not np.array_equal(arrays[0][~footprint], arrays[1][~footprint]):
                    raise RuntimeError('RGBD codec changed decoded pixels outside the selected region')
                metrics['rgbd_region']['outside_decoded_exact'] = True
            info = call([ktx, 'info', str(output)])
            (run / (Path(spec['output']).stem + '_ktx_info.txt')).write_text(info, encoding='utf8')
            report['textures'].append(dict(**spec, **metrics, output_bytes=output.stat().st_size, output_sha256=sha(output)))
            save()
        for model in plan['models']:
            # Lossy GPU formats can undershoot the smallest valid alpha divisor.
            # Record the pre-compression bound, so clients cannot amplify that error
            # beyond the source encoding's representable range.
            apply_rgbd_bounds(model['data'], report['textures'])
            (staging / model['output']).write_text(json.dumps(model['data'], ensure_ascii=False, separators=(',', ':')), encoding='utf8')
            if sha(Path(model['source'])) != model['source_sha256']:
                raise RuntimeError('Source model changed during conversion')
        for spec in report['textures']:
            if sha(Path(spec['source_path'])) != spec['source_sha256']:
                raise RuntimeError('Source texture changed during conversion')
        files = list(staging.iterdir())
        commit_delivery(files, Path(plan['output_dir']), run)
        report.update(status='passed', sources_unchanged=True,
                      delivery_files=[dict(path=str(Path(plan['output_dir']) / f.name), sha256=sha(f), bytes=f.stat().st_size) for f in files])
        save()
        (root / 'CONVERSION_LATEST.json').write_text(json.dumps(report, indent=2), encoding='utf8')
        print('CONVERSION_COMPLETE', str(run), flush=True)
        return report
    except Exception as exc:
        report.update(status='failed', error=str(exc))
        save()
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path(__file__).with_name('texture_conversion.json'))
    parser.add_argument('--inspect', type=Path, metavar='MODEL')
    parser.add_argument('--plan', action='store_true')
    parser.add_argument('--ktx', help='KTX executable, overrides config and PATH')
    parser.add_argument('--threads', type=int, default=8)
    args = parser.parse_args()
    if args.inspect:
        print(json.dumps(inspect_model(read_json(args.inspect)), indent=2))
        return
    if args.threads < 1:
        parser.error('--threads must be positive')
    plan = load_plan(args.config)
    if args.plan:
        print(json.dumps({**plan, 'models': [{k:v for k,v in m.items() if k != 'data'} for m in plan['models']]}, indent=2))
        return
    config = read_json(args.config)
    ktx = args.ktx or config.get('tools', {}).get('windows_ktx' if os.name == 'nt' else 'linux_ktx') or shutil.which('ktx')
    if not ktx:
        parser.error('KTX-Software is required; supply --ktx or configure tools')
    run_conversion(plan, ktx, args.threads)

def colour_codec(options):
    value = options.get('colour_adjustment_codec', 'INHERIT')
    value = options.get('codec', 'basis-lz') if value == 'INHERIT' else value
    if value not in ('basis-lz', 'uastc'):
        raise ValueError('Colour adjustment codec must be INHERIT, basis-lz or uastc')
    return value


def load_region_policy(path):
    """Bind a declarative policy and ownership inputs before conversion starts."""
    path = Path(path).resolve()
    policy = read_json(path)
    if policy.get('schema_version') != 1 or policy.get('encoding') != 'rgbd-v1':
        raise ValueError('Expected region schema_version 1 and rgbd-v1 encoding')
    result = dict(policy, policy_path=str(path), policy_sha256=sha(path))
    for key in ('ownership_labels', 'islands'):
        source = (path.parent / policy[key]).resolve()
        if not source.is_file():
            raise ValueError('Missing region dependency: ' + str(source))
        expected = policy.get(key + '_sha256')
        if expected and sha(source) != expected:
            raise ValueError('Region dependency identity mismatch: ' + key)
        result[key] = str(source)
        result[key + '_sha256'] = sha(source)
    if policy.get('geometry_reference'):
        source = (path.parent / policy['geometry_reference']).resolve()
        if not source.is_file(): raise ValueError('Missing region geometry reference')
        result['geometry_reference'] = str(source)
        result['geometry_reference_sha256'] = sha(source)
    return result


def encode_rgbd_regions(rgb, original, policy):
    """Change complete 4x4 blocks only; protect all nonselected owned pixels.

    Labels and RGB use top-down image orientation. A one-pixel nearest guard
    uses the same tie order as scipy's EDT (left, up, down, right), without a
    scipy dependency in Blender. Ownership IDs and object selectors are data.
    """
    import io
    import numpy as np
    rgb = np.asarray(rgb, dtype=np.float32)
    h, w = rgb.shape[:2]
    if rgb.shape != (h, w, 3) or original.shape != (h, w, 4) or original.dtype != np.uint8:
        raise ValueError('Region RGB and original RGBA8 dimensions must match')
    if h % 4 or w % 4 or not np.isfinite(rgb).all() or (rgb < 0).any():
        raise ValueError('Region encoding requires finite positive RGB and dimensions divisible by four')
    if policy.get('block_size', 4) != 4 or policy.get('guard_pixels', 1) != 1:
        raise ValueError('Region encoding supports 4x4 blocks and one-pixel nearest padding')
    for key in ('ownership_labels', 'islands'):
        if sha(Path(policy[key])) != policy.get(key + '_sha256'):
            raise ValueError('Region input changed: ' + key)
    if policy.get('policy_path') and sha(Path(policy['policy_path'])) != policy['policy_sha256']:
        raise ValueError('Region policy changed during conversion')
    loaded = np.load(policy['ownership_labels'], allow_pickle=False)
    if isinstance(loaded, np.lib.npyio.NpzFile):
        try:
            if loaded.files != ['labels']: raise ValueError('Ownership NPZ must contain labels only')
            labels = loaded['labels']
        finally: loaded.close()
    else: labels = loaded
    if policy.get('ownership_array_npy_sha256'):
        stream = io.BytesIO(); np.save(stream, labels)
        if hashlib.sha256(stream.getvalue()).hexdigest() != policy['ownership_array_npy_sha256']:
            raise ValueError('Ownership array identity mismatch')
    if labels.ndim != 2 or not np.isfinite(labels).all() or (labels < 0).any() or not np.equal(labels, np.floor(labels)).all():
        raise ValueError('Ownership must contain nonnegative integer IDs')
    if labels.shape != (h, w):
        sh, sw = labels.shape
        if sh % h or sw % w or sh // h != sw // w or sh < h:
            raise ValueError('Ownership cannot be matched to the output resolution')
        scale = sh // h
        labels = labels[scale//2::scale, scale//2::scale]
    islands = read_json(Path(policy['islands']))
    ids = [row['id'] for row in islands]
    if len(set(ids)) != len(ids) or any(type(v) is not int or v <= 0 for v in ids):
        raise ValueError('Island catalogue IDs must be unique positive integers')
    if not set(np.unique(labels)) - {0} <= set(ids):
        raise ValueError('Ownership IDs are absent from the matching catalogue')
    if policy.get('geometry_reference'):
        if sha(Path(policy['geometry_reference'])) != policy['geometry_reference_sha256']:
            raise ValueError('Region geometry reference changed')
        geometry = read_json(Path(policy['geometry_reference']))
        if geometry.get('geometry_sha256') != policy.get('geometry_sha256'):
            raise ValueError('Region geometry identity mismatch')
    def selected_ids(spec):
        wanted = set(spec.get('ids', []))
        names, prefixes = spec.get('objects', []), tuple(spec.get('object_prefixes', []))
        wanted.update(row['id'] for row in islands if row.get('object') in names or
                      (prefixes and row.get('object', '').startswith(prefixes)))
        if not wanted or not wanted <= set(ids): raise ValueError('Region selector is empty or references unknown IDs')
        return sorted(wanted)
    selected = selected_ids(policy['selection'])
    special = selected_ids(policy['stepped_selection']) if policy.get('stepped_selection') else []
    if not set(special) <= set(selected): raise ValueError('Stepped region must be within selected ownership')
    guard = labels.copy()
    # Only unowned pixels receive padding. Lexicographic nearest ties match EDT.
    for dy, dx in ((0,-1),(-1,0),(1,0),(0,1)):
        shifted = np.zeros_like(labels)
        y0,y1=max(0,-dy),min(h,h-dy); x0,x1=max(0,-dx),min(w,w-dx)
        shifted[y0:y1,x0:x1] = labels[y0+dy:y1+dy,x0+dx:x1+dx]
        use = (guard == 0) & (shifted > 0)
        guard[use] = shifted[use]
    def blocks(mask): return mask.reshape(h//4,4,w//4,4).any(axis=(1,3))
    fixture_blocks = blocks(np.isin(guard, selected))
    protected = blocks((labels > 0) & ~np.isin(labels, selected))
    affected = fixture_blocks & ~protected
    if not affected.any(): raise ValueError('Selected ownership has no unprotected codec blocks')
    safe = np.minimum(np.floor(255 / np.maximum(rgb.reshape(h//4,4,w//4,4,3).max(axis=(1,3,4)),1e-7)),255).astype(np.uint8)
    minimum = policy.get('minimum_alpha_code', 3)
    if type(minimum) is not int or not 1 <= minimum <= 255 or (safe[affected] < minimum).any():
        raise ValueError('Selected RGBD radiance exceeds the configured minimum divisor; source is not clipped')
    codes = safe.copy()
    if special:
        steps = np.array(policy.get('alpha_steps', [3,7,15,31,63,127,255]))
        if steps.ndim != 1 or not len(steps) or not np.equal(steps,np.floor(steps)).all() or steps[0] < minimum or steps[-1] > 255 or (np.diff(steps) <= 0).any():
            raise ValueError('Alpha steps must be increasing integer codes within the allowed range')
        stepped = blocks(np.isin(guard, special)) & affected
        indices = np.searchsorted(steps, safe[stepped], side='right') - 1
        if (indices < 0).any(): raise ValueError('No representable stepped divisor for selected radiance')
        codes[stepped] = steps[indices]
    alpha = codes.repeat(4,0).repeat(4,1)
    scaled = rgb * (alpha.astype(np.float32)/255)[...,None]
    footprint = affected.repeat(4,0).repeat(4,1)
    if not np.isfinite(scaled[footprint]).all() or (scaled[footprint] > 1.000001).any():
        raise ValueError('Region RGBD would clip source radiance')
    pixels = original.copy()
    encoded = np.dstack((np.rint(np.power(np.clip(scaled,0,1),1/2.2)*255).astype(np.uint8),alpha))
    pixels[footprint] = encoded[footprint]
    if not np.array_equal(pixels[~footprint], original[~footprint]):
        raise RuntimeError('Region encoding changed pixels outside selected codec blocks')
    return pixels, footprint, dict(rgbd_region=dict(affected_blocks=int(affected.sum()),
        mixed_blocks_retained_original=int((fixture_blocks & protected).sum()), outside_prepared_exact=True,
        policy_sha256=policy.get('policy_sha256'), source_radiance_clipped=False),
        alpha_min=int(pixels[...,3].min()), alpha_max=int(pixels[...,3].max()))


def region_artifacts(original, selected, spec, directory, write_png):
    """Keep baseline inputs so the real codec can verify decoded outside pixels."""
    import numpy as np
    stem = Path(spec['output']).stem
    baseline = directory / (stem + '_region_baseline.png')
    footprint = directory / (stem + '_region_footprint.npy')
    write_png(baseline, original[::-1].copy() if spec['flip_y'] else original)
    np.save(footprint, selected[::-1].copy() if spec['flip_y'] else selected)
    return dict(rgbd_region_baseline=str(baseline), rgbd_region_footprint=str(footprint))


if __name__ == '__main__':
    main()
