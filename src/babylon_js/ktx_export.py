"""Optional staged KTX export. No third-party Python packages beyond Blender's numpy."""
from datetime import datetime, timezone
from functools import lru_cache
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import zlib

from . import ktx_conversion as core


def process_options():
    return {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}


@lru_cache(maxsize=8)
def _probe(path, modified):
    try:
        result = subprocess.run([path, '--version'], capture_output=True, text=True,
                                timeout=5, **process_options())
        version = result.stdout.strip()
        match = re.search(r'ktx version:\s*v?(\d+)\.(\d+)\.(\d+)', version)
        if result.returncode or not match:
            return '', 'Selected executable is not a working Khronos ktx tool'
        if tuple(map(int, match.groups())) < (4, 4, 2):
            return '', 'KTX 4.4.2 or newer required (found ' + version + ')'
        return path, version
    except (OSError, subprocess.SubprocessError) as exc:
        return '', 'Cannot run ktx: ' + str(exc)


def find_ktx(override=''):
    import bpy
    paths = [bpy.path.abspath(override)] if override else [
        shutil.which('ktx', path=folder) for folder in os.environ.get('PATH', '').split(os.pathsep) if folder]
    reason = 'Install KTX-Software 4.4.2+ on PATH or select its executable'
    for path in dict.fromkeys(p for p in paths if p):
        try:
            tool, reason = _probe(str(Path(path).resolve()), Path(path).stat().st_mtime_ns)
            if tool:
                return tool, reason
        except OSError:
            reason = 'KTX executable does not exist: ' + str(path)
    return '', reason


def _write_png(path, pixels):
    height, width, channels = pixels.shape
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    rows = b''.join(b'\0' + row.tobytes() for row in pixels)
    data = b'\x89PNG\r\n\x1a\n'
    data += chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6 if channels == 4 else 2, 0, 0, 0))
    data += chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b'')
    path.write_bytes(data)


def prepare_blender(spec, directory):
    """Read an isolated image datablock, preserving PNG codes and linear HDR values."""
    import bpy
    import numpy as np
    source = Path(spec['source_path'])
    source_hash = core.sha(source)
    image = bpy.data.images.load(str(source), check_existing=False)
    try:
        # Non-Color prevents Blender decoding LDR code values to scene-linear.
        # EXR/HDR buffers already contain linear radiometric values.
        image.colorspace_settings.name = 'Non-Color'
        width, height = image.size
        if not width or not height or image.channels not in (3, 4):
            raise ValueError('Unsupported image layout: ' + str(source))
        array = np.empty(width * height * 4, dtype=np.float32)
        image.pixels.foreach_get(array)
        array = array.reshape(height, width, 4)[::-1].copy()  # Blender bottom-up -> PNG top-down
        # Blender also exposes 16-bit PNG buffers as floating point. Their
        # stored code values are not HDR and must not receive an sRGB transform.
        hdr = image.is_float and source.suffix.lower() != '.png'
    finally:
        bpy.data.images.remove(image)
    if not np.isfinite(array).all():
        raise ValueError('Non-finite texture pixels: ' + str(source))
    metrics = {}
    if spec.get('encoding') == 'rgbd-v1':
        if not hdr:
            raise ValueError('RGBD lightmaps require a linear HDR/EXR source')
        pixels, metrics = core.encode_rgbd(array[:, :, :3])
    else:
        if hdr:
            rgb = array[:, :, :3]
            metrics = dict(linear_min=float(rgb.min()), linear_max=float(rgb.max()),
                           pixels_above_one_percent=float(np.mean(np.any(rgb > 1, axis=2)) * 100))
            rgb = np.clip(rgb, 0, 1)
            if spec['color_space'] == 'srgb':
                rgb = np.where(rgb <= 0.0031308, rgb * 12.92, 1.055 * np.power(rgb, 1 / 2.4) - 0.055)
            array[:, :, :3] = rgb
        if spec['alpha'] == 'opaque' and not np.all(array[:, :, 3] == 1):
            raise ValueError('Non-opaque source: ' + str(source))
        if spec['alpha'] != 'preserve' or np.all(array[:, :, 3] == 1):
            array = array[:, :, :3]
        with source.open('rb') as stream:
            header = stream.read(29)
        png16 = header.startswith(b'\x89PNG\r\n\x1a\n') and header[24] == 16
        if png16 and not hdr:
            # Match the established Pillow PNG baseline: take the high byte of
            # each 16-bit channel, rather than introduce different 8-bit rounding.
            pixels = (np.rint(np.clip(array, 0, 1) * 65535).astype(np.uint16) >> 8).astype(np.uint8)
            metrics['png_16_to_8'] = 'high byte (baseline)'
        else:
            pixels = np.rint(np.clip(array, 0, 1) * 255).astype(np.uint8)
    stem = Path(spec['output']).stem
    _write_png(directory / (stem + '_unflipped.png'), pixels)
    if spec['flip_y']:
        pixels = pixels[::-1].copy()
    prepared = directory / (stem + '_prepared.png')
    _write_png(prepared, pixels)
    if core.sha(source) != source_hash:
        raise RuntimeError('Source changed while preparing image')
    return prepared, dict(source_sha256=source_hash, dimensions=[width, height], channels=pixels.shape[2],
                          prepared=str(prepared), prepared_sha256=core.sha(prepared), **metrics)


LINEAR_SLOTS = {'metallictexture', 'roughnesstexture', 'ambienttexture', 'opacitytexture',
                'bumptexture', 'normaltexture', 'occlusiontexture', 'thicknesstexture'}


def infer_lightmap(context, objects, marker_name=''):
    """Find a unique candidate by explicit tag, marker name, UV2 use, then keyword.

    Never rank historical images by file date or image-list order. An image must
    have an existing source file: saving an unsaved/packed-only bake is a separate
    user decision. One automatic lightmap is supported by the current dialog.
    """
    import bpy
    objects = list(objects)
    markers = [o for o in objects if o.name.startswith('lightmap_') and (not marker_name or o.name == marker_name)]
    if not markers:
        return dict(path='', marker='', reason='No exported lightmap_ node', ambiguous=False)
    if len(markers) != 1:
        return dict(path='', marker='', reason='Several lightmap nodes: select a marker and image', ambiguous=True)
    marker = markers[0]
    def image_path(image):
        path = Path(bpy.path.abspath(image.filepath)) if image and image.filepath else None
        return str(path.resolve()) if path and path.is_file() else ''
    def choose(candidates, reason):
        paths = sorted(set(p for p in candidates if p))
        return dict(path=paths[0] if len(paths)==1 else '', marker=marker.name,
                    reason=reason if len(paths)==1 else 'Several candidates from ' + reason + ': select the lightmap image',
                    ambiguous=len(paths)>1)
    explicit = marker.get('bjs_lightmap_image')
    if explicit:
        image = bpy.data.images.get(str(explicit))
        path = image_path(image) if image else str(Path(bpy.path.abspath(str(explicit))).resolve())
        if not path or not Path(path).is_file():
            return dict(path='', marker=marker.name, reason='bjs_lightmap_image does not identify an existing image file', ambiguous=True)
        return choose([path], 'lightmap node bjs_lightmap_image property')
    stem = marker.name[len('lightmap_'):]
    matches = [image_path(image) for image in bpy.data.images
               if image.name == stem or (image.filepath and Path(bpy.path.abspath(image.filepath)).stem == stem)]
    if any(matches):
        return choose(matches, 'image name matching the lightmap node')
    uv2_candidates = []
    def upstream(node, seen=None):
        seen = set() if seen is None else seen
        if node in seen:
            return []
        seen.add(node)
        return [node] + [n for socket in node.inputs for link in socket.links for n in upstream(link.from_node, seen)]
    for obj in objects:
        if obj.type != 'MESH' or len(obj.data.uv_layers)<2:
            continue
        uv2 = obj.data.uv_layers[1].name
        for slot in obj.material_slots:
            mat = slot.material
            if not mat or not mat.use_nodes:
                continue
            used = {node for output in mat.node_tree.nodes if output.type=='OUTPUT_MATERIAL' and output.is_active_output
                    for node in upstream(output)}
            for node in used:
                if node.type != 'TEX_IMAGE' or not node.image:
                    continue
                vector = node.inputs.get('Vector')
                mappings = [n for link in vector.links for n in upstream(link.from_node)] if vector else []
                if any(n.type=='UVMAP' and n.uv_map==uv2 for n in mappings):
                    uv2_candidates.append(image_path(node.image))
    if any(uv2_candidates):
        return choose(uv2_candidates, 'material image explicitly connected to UV2')
    keywords = [image_path(image) for image in bpy.data.images
                if any(word in (image.name + ' ' + Path(image.filepath).name).lower() for word in ('lightmap','simplebake'))]
    if any(keywords):
        return choose(keywords, 'lightmap/SimpleBake image keyword')
    return dict(path='', marker=marker.name, reason='No unique saved lightmap found; choose its source image', ambiguous=False)


def autofill_lightmap(operator, context):
    if not operator.convert_to_ktx2 or not operator.ktx_auto_lightmap or operator.ktx_lightmap:
        return
    objects = context.selected_objects if operator.export_selected else context.scene.objects
    result = infer_lightmap(context, objects, operator.ktx_lightmap_marker)
    if result['path']:
        operator.ktx_lightmap = result['path']
        operator.ktx_lightmap_marker = result['marker']


def _check_texture_collisions(exporter):
    identities = {}
    for material in exporter.materials:
        for texture in material.textures.values():
            if not hasattr(texture, 'fileNoPath'):
                continue
            import bpy
            image = texture.image
            identity = ('packed', image.as_pointer()) if image.packed_file else ('file', bpy.path.abspath(image.filepath))
            key = texture.fileNoPath.replace('\\', '/').casefold()
            if key in identities and identities[key] != identity:
                raise ValueError('Different images export to the same filename: ' + texture.fileNoPath)
            identities[key] = identity


def make_config(raw_model, work, options, context=None, objects=()):
    """Infer safe material defaults; lightmap input is explicit, never guessed from names."""
    import bpy
    model = core.read_json(raw_model)
    specs, seen = [], {}
    for owner, key, texture, location in core.texture_fields({'materials': model.get('materials', [])}):
        if texture is None or texture.get('isCube') or 'reflectionTexture' in location or 'refractionTexture' in location:
            continue
        reference = owner[key]
        if Path(reference).suffix.lower() in ('.ktx2', '.dds', '.basis'):
            continue
        source = (raw_model.parent / reference).resolve()
        if not source.is_relative_to(raw_model.parent.resolve()) or not source.is_file():
            raise ValueError('Exported texture is missing or outside staging: ' + reference)
        slot = location.rsplit('.', 1)[0].rsplit('.', 1)[-1].lower().lstrip('_')
        lightmap = slot == 'lightmaptexture'
        linear = slot in LINEAR_SLOTS or texture.get('gammaSpace') is False
        if 'clearCoat.bumpTexture' in location or 'roughness.texture' in location:
            linear = True
        policy = ('linear' if linear else 'srgb', 'uastc' if lightmap or slot in ('bumptexture','normaltexture') else options.get('codec','basis-lz'), not lightmap)
        if reference in seen:
            if seen[reference] != policy:
                raise ValueError('One image has conflicting colour/data uses; separate it or use the standalone manifest: ' + reference)
            if texture.get('hasAlpha', False):
                # A shared atlas can be opaque on one material and a cutout on another.
                next(spec for spec in specs if reference in spec.get('references', []))['alpha'] = 'preserve'
            continue
        seen[reference] = policy
        # Opaque sources are common, but preserve alpha unless known not to be used.
        specs.append(dict(source=str(source), output=Path(reference).stem + '.ktx2', references=[reference],
                          flip_y=options.get('flip_y', True), color_space=policy[0], codec=policy[1], mipmaps=policy[2],
                          alpha='preserve' if texture.get('hasAlpha', False) else 'discard'))
    lightmap = options.get('lightmap', '')
    if not lightmap and options.get('auto_lightmap', True) and context:
        exported_names = {n['name'] for collection in ('meshes','transformNodes') for n in model.get(collection, [])}
        result = infer_lightmap(context, [o for o in objects if o.name in exported_names], options.get('lightmap_marker', ''))
        options['lightmap_detection'] = result
        if result['ambiguous']:
            raise ValueError(result['reason'])
        if result['path']:
            lightmap = result['path']
            options['lightmap_marker'] = result['marker']
    if lightmap:
        source = Path(bpy.path.abspath(lightmap)).resolve()
        if not source.is_file():
            raise ValueError('KaDshow lightmap image does not exist: ' + str(source))
        names = core.inspect_model(model)['lightmap_markers']
        marker = options.get('lightmap_marker', '')
        if not marker:
            if len(names) != 1:
                raise ValueError('Select an exact lightmap marker when the export does not have exactly one')
            marker = names[0]
        if marker not in names:
            raise ValueError('Lightmap marker is not in the exported selection: ' + marker)
        encoding = options.get('lightmap_encoding', 'legacy')
        if encoding not in ('legacy', 'rgbd-v1'):
            raise ValueError('Unsupported lightmap encoding: ' + str(encoding))
        rgbd = encoding == 'rgbd-v1'
        output = core.lightmap_output_name(source.stem + ('_rgbd' if rgbd else '') + '.ktx2')
        specs.append(dict(source=str(source), output=output, lightmap_markers=[marker], encoding=encoding,
                          flip_y=options.get('flip_y', True), color_space='linear' if rgbd else 'srgb',
                          codec='uastc', mipmaps=False, alpha='preserve' if rgbd else 'discard'))
    return dict(version=1, output_dir=str(work / 'converted'),
                models=[dict(source=str(raw_model), output=raw_model.name)], textures=specs)


def publish_package(package, destination, backup):
    """Copy the finished set with rollback. Preserve unrelated files in destination."""
    previous, attempted = {}, []
    files = [p for p in package.rglob('*') if p.is_file()]
    for source in files:
        relative = source.relative_to(package)
        target = destination / relative
        if not target.resolve().is_relative_to(destination.resolve()):
            raise ValueError('Destination symlink escapes export folder: ' + str(target))
        if target.exists():
            saved = backup / relative
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, saved)
            previous[relative] = saved
    try:
        for source in files:
            relative = source.relative_to(package)
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            attempted.append(relative)
            shutil.copy2(source, target)
            if core.sha(source) != core.sha(target):
                raise RuntimeError('Delivery copy failed verification')
    except Exception:
        for relative in reversed(attempted):
            target = destination / relative
            if relative in previous:
                shutil.copy2(previous[relative], target)
            elif target.is_file():
                target.unlink()
        raise


def execute_with_ktx(exporter, context, filepath, objects, options, material_options=None):
    """Called by JsonExporter only when the option is explicitly enabled."""
    import bpy
    exporter.fatalError = None
    exporter.nErrors = exporter.nWarnings = 0
    work = None
    try:
        convert_materials = options.get('convert_materials', True)
        tool, message = '', 'Material conversion disabled'
        if convert_materials:
            tool, message = find_ktx(options.get('executable', ''))
            if not tool:
                raise ValueError(message)
        if 'skybox' in options:
            from .skybox_export import find_basisu
            basis, reason = find_basisu(options['skybox'].get('executable', ''))
            if not basis:
                raise ValueError(reason)
        if 'environment' in options:
            from .env_export import find_converter
            node, script, reason = find_converter(options['environment'].get('converter', ''))
            if not node:
                raise ValueError(reason)
        settings = context.scene.world
        if settings.inlineTextures:
            raise ValueError('Disable Inline textures before enabling staged texture/environment conversion')
        target = Path(filepath).resolve()
        if target.suffix.lower() != '.babylon':
            raise ValueError('Choose a .babylon output filename')
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
        work = target.parent / '.bjs-ktx' / target.stem / stamp
        raw = work / 'source_export' / target.name
        raw.parent.mkdir(parents=True)
        old_dir = settings.textureDir
        try:
            settings.textureDir = 'source_textures'  # contained staging regardless of user's textureDir
            objects = list(objects)
            exporter.execute(context, str(raw), objects, material_options=material_options)
        finally:
            settings.textureDir = old_dir
        if exporter.fatalError or exporter.nErrors:
            raise RuntimeError(exporter.fatalError or 'Exporter reported data errors; see staging log')
        _check_texture_collisions(exporter)
        config = make_config(raw, work, options, context, objects) if convert_materials else dict(
            version=1, models=[dict(source=str(raw), output=raw.name)], textures=[])
        detection = options.get('lightmap_detection')
        if detection and detection['marker'] and not detection['path']:
            exporter.nWarnings += 1
        config_path = work / 'texture_conversion.json'
        config_path.write_text(json.dumps(config, indent=2), encoding='utf8')
        package = work / 'package'
        package.mkdir()
        if config['textures']:
            plan = core.load_plan(config_path)
            result = core.run_conversion(plan, tool, options.get('threads', 8), prepare_blender)
            for file in (work / 'converted').iterdir():
                shutil.copy2(file, package / file.name)
        else:
            shutil.copy2(raw, package / target.name)
            result = dict(status='passed', textures=[], note='No convertible material textures')
        # Keep unconverted referenced images (e.g. World HDRI) at their original
        # relative paths. Do not copy redundant source material PNGs to delivery.
        delivered = core.read_json(package / target.name)
        skybox_report = None
        if 'skybox' in options:
            from .skybox_export import export_skybox
            skybox_report = export_skybox(context, delivered, work, package, options['skybox'])
            (package / target.name).write_text(json.dumps(delivered, ensure_ascii=False), encoding='utf8')
        environment_report = None
        if 'environment' in options:
            from .env_export import export_environment
            environment_report = export_environment(context, delivered, work, package, options['environment'])
            # The custom KaDshow environment loader uses hasenv/environment.env.
            # Avoid delivering a second raw World panorama as a stock scene default.
            for key in list(delivered):
                if key.startswith('environmentTexture'):
                    del delivered[key]
            (package / target.name).write_text(json.dumps(delivered, ensure_ascii=False), encoding='utf8')
        for owner, key, _, _ in core.texture_fields(delivered):
            reference = owner[key]
            if '://' in reference or reference.startswith('data:'):
                continue
            destination = (package / reference).resolve()
            if not destination.is_relative_to(package.resolve()):
                raise ValueError('Unconverted asset escapes package: ' + reference)
            if destination.is_file():
                continue
            source = (raw.parent / reference).resolve()
            if not source.is_relative_to(raw.parent.resolve()) or not source.is_file():
                raise ValueError('Missing unconverted texture: ' + reference)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        for file in raw.parent.iterdir():
            if file.suffix in ('.log', '.csv', '.manifest'):
                shutil.copy2(file, package / file.name)
        summary = dict(status='passed', ktx=message, staging=str(work), options=options,
                       material_multipliers=exporter.material_options,
                       textures=result['textures'], model=str(target), changes=result.get('changes', []))
        if skybox_report:
            summary['skybox'] = skybox_report
        if environment_report:
            summary['environment'] = environment_report
        (package / (target.stem + '.ktx-report.json')).write_text(json.dumps(summary, indent=2), encoding='utf8')
        publish_package(package, target.parent, work / 'previous_delivery')
        exporter.ktx_report = summary
    except Exception as exc:
        exporter.fatalError = 'Texture/skybox export failed: ' + str(exc)
        exporter.nErrors = max(1, exporter.nErrors)
        if work:
            (work / 'FAILED.txt').write_text(exporter.fatalError, encoding='utf8')
            exporter.fatalError += ' (details: ' + str(work) + ')'
