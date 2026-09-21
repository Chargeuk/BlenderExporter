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
        spec['output'] = output_name(spec['output'], '.ktx2')
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
        rgb = raw[:, :, [2, 1, 0]].astype(np.float32)
        if not np.isfinite(rgb).all():
            raise ValueError(f'Non-finite RGB values: {source}')
        metrics = dict(linear_min=float(rgb.min()), linear_max=float(rgb.max()),
                       pixels_above_one_percent=float(np.mean(np.any(rgb > 1, axis=2)) * 100))
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
    return prepared, dict(source_sha256=sha(source), dimensions=list(image.size),
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
            info = call([ktx, 'info', str(output)])
            (run / (Path(spec['output']).stem + '_ktx_info.txt')).write_text(info, encoding='utf8')
            report['textures'].append(dict(**spec, **metrics, output_bytes=output.stat().st_size, output_sha256=sha(output)))
            save()
        for model in plan['models']:
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


if __name__ == '__main__':
    main()
