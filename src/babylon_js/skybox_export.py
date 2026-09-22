"""Optional panorama -> six faces -> ETC1S .basis, using Blender and basisu.

Projection is implemented from standard cubemap direction vectors. Longitude
uses the legacy panorama-to-cubemap website's orientation (180 degrees default).
No browser, render job, or additional Python dependency is required.
"""
from functools import lru_cache
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess

from . import ktx_conversion as core
from .ktx_export import _write_png, process_options

FACE_NAMES = ('px', 'nx', 'py', 'ny', 'pz', 'nz')
FACE_SIZES = (256, 512, 1024, 2048, 4096)


@lru_cache(maxsize=8)
def _probe(path, modified):
    try:
        proc = subprocess.run([path, '-help'], capture_output=True, text=True,
                              timeout=10, **process_options())
        text = proc.stdout + proc.stderr
        if proc.returncode or not all(token in text for token in
                                      ('Basis Universal', '-basis', '-tex_array', '-tex_type', '-etc1s')):
            return '', 'Select a basisu supporting -basis, ETC1S and cubemaps (tested: 2.50)'
        return path, text.splitlines()[0]
    except (OSError, subprocess.SubprocessError) as exc:
        return '', 'Cannot run basisu: ' + str(exc)


def find_basisu(override=''):
    import bpy
    candidates = [bpy.path.abspath(override)] if override else [
        shutil.which('basisu', path=p) for p in os.environ.get('PATH', '').split(os.pathsep) if p]
    reason = 'Install Basis Universal on PATH or select its executable'
    for path in dict.fromkeys(p for p in candidates if p):
        try:
            tool, reason = _probe(str(Path(path).resolve()), Path(path).stat().st_mtime_ns)
            if tool:
                return tool, reason
        except OSError:
            reason = 'Basis executable does not exist: ' + str(path)
    return '', reason


def world_panorama(context):
    """Suggest only a unique connected, external, equirectangular World image.

    This resolves an image, not an evaluation of World mapping/strength/nodes.
    Rotation and exposure are explicit skybox settings.
    """
    import bpy
    world = context.scene.world
    from .environment_controls import find_controls, source_image
    managed = find_controls(world)
    if managed:
        image = source_image(managed)
        return bpy.path.abspath(image.filepath, library=image.library)
    if not world or not world.use_nodes:
        return ''
    pending = [n for n in world.node_tree.nodes if n.type == 'OUTPUT_WORLD' and n.is_active_output]
    seen, paths = set(), set()
    while pending:
        node = pending.pop()
        if node in seen:
            continue
        seen.add(node)
        if node.type == 'TEX_ENVIRONMENT' and node.projection == 'EQUIRECTANGULAR' and node.image:
            image = node.image
            if image.filepath:
                path = Path(bpy.path.abspath(image.filepath, library=image.library))
                if path.is_file():
                    paths.add(str(path.resolve()))
        pending.extend(link.from_node for socket in node.inputs for link in socket.links)
    return next(iter(paths)) if len(paths) == 1 else ''


def autofill_panorama(operator, context):
    if operator.export_skybox and not operator.skybox_image:
        operator.skybox_image = world_panorama(context)


def read_panorama(source):
    import bpy
    import numpy as np
    image = bpy.data.images.load(str(source), check_existing=False)
    try:
        image.colorspace_settings.name = 'Non-Color'
        w, h = image.size
        if not w or w != 2 * h:
            raise ValueError('Skybox source must be a 2:1 equirectangular panorama')
        if image.channels not in (3, 4):
            raise ValueError('Skybox source must be RGB or RGBA')
        data = np.empty(w * h * 4, dtype=np.float32)
        image.pixels.foreach_get(data)
        data = data.reshape(h, w, 4)[::-1, :, :3].copy()
        hdr = source.suffix.lower() in ('.exr', '.hdr')
    finally:
        bpy.data.images.remove(image)
    if not np.isfinite(data).all():
        raise ValueError('Skybox source contains non-finite pixels')
    # Work in linear light for interpolation and exposure. LDR input is sRGB.
    if not hdr:
        data = np.where(data <= .04045, data / 12.92, ((data + .055) / 1.055) ** 2.4)
    return data, hdr


def face_directions(face, size, first_row=0, last_row=None):
    """Top-down standard +X,-X,+Y,-Y,+Z,-Z cube face direction vectors."""
    import numpy as np
    last_row = size if last_row is None else last_row
    u, v = np.meshgrid(2 * (np.arange(size, dtype=np.float32) + .5) / size - 1,
                      2 * (np.arange(first_row, last_row, dtype=np.float32) + .5) / size - 1)
    one = np.ones_like(u)
    directions = {'px': (one, -v, -u), 'nx': (-one, -v, u),
                  'py': (u, one, v), 'ny': (u, -one, -v),
                  'pz': (u, -v, one), 'nz': (-u, -v, -one)}
    return np.stack(directions[face], axis=-1)


def sample_panorama(panorama, directions, rotation=180):
    """Bilinear linear-light sampling with horizontal wrap and pole clamping."""
    import numpy as np
    h, w = panorama.shape[:2]
    d = directions / np.linalg.norm(directions, axis=-1, keepdims=True)
    longitude = np.arctan2(d[..., 0], d[..., 2]) + math.radians(rotation - 180)
    x = np.mod(longitude / (2 * math.pi), 1) * w - .5
    y = np.clip(np.arccos(np.clip(d[..., 1], -1, 1)) / math.pi * h - .5, 0, h - 1)
    x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int)
    fx, fy = (x - x0)[..., None], (y - y0)[..., None]
    a = panorama[y0, x0 % w] * (1-fx) + panorama[y0, (x0+1) % w] * fx
    b = panorama[np.minimum(y0+1, h-1), x0 % w] * (1-fx) + panorama[np.minimum(y0+1, h-1), (x0+1) % w] * fx
    return a * (1-fy) + b * fy


def display_pixels(linear, hdr, exposure, tone_map):
    import numpy as np
    rgb = np.maximum(linear * (2.0 ** exposure), 0)
    if hdr and tone_map == 'REINHARD':
        # Luminance-based compression retains RGB ratios before display clipping.
        luminance = rgb @ np.array([.2126, .7152, .0722])
        rgb = rgb / (1 + luminance[..., None])
    clipped = int(np.count_nonzero(np.any(rgb > 1, axis=-1)))
    rgb = np.clip(rgb, 0, 1)
    rgb = np.where(rgb <= .0031308, rgb * 12.92, 1.055 * rgb ** (1/2.4) - .055)
    return np.rint(rgb * 255).astype(np.uint8), clipped


def prepare_faces(source, directory, size=1024, rotation=180, exposure=0, tone_map='REINHARD'):
    import numpy as np
    if size not in FACE_SIZES:
        raise ValueError('Skybox face size must be one of: ' + str(FACE_SIZES))
    if tone_map not in ('REINHARD', 'STANDARD') or not math.isfinite(rotation) or not math.isfinite(exposure) or not -20 <= exposure <= 20:
        raise ValueError('Invalid skybox rotation/exposure/tone mapping')
    source_hash = core.sha(source)
    panorama, hdr = read_panorama(source)
    directory.mkdir(parents=True, exist_ok=True)
    faces, clipped = [], 0
    for face in FACE_NAMES:
        output = np.empty((size, size, 3), dtype=np.uint8)
        # Bound temporary projection memory even at the largest selectable size.
        for row in range(0, size, 64):
            directions = face_directions(face, size, row, min(size, row+64))
            sampled = sample_panorama(panorama, directions, rotation)
            pixels, count = display_pixels(sampled, hdr, exposure, tone_map)
            output[row:row+len(pixels)] = pixels
            clipped += count
        path = directory / (face + '.png')
        _write_png(path, output)
        faces.append(path)
    if core.sha(source) != source_hash:
        raise RuntimeError('Skybox source changed during conversion')
    return faces, dict(source=str(source), source_sha256=source_hash,
                      source_dimensions=[panorama.shape[1], panorama.shape[0]], source_hdr=hdr,
                      face_size=size, face_order=list(FACE_NAMES), rotation_degrees=rotation,
                      exposure_stops=exposure, tone_map=tone_map if hdr else 'sRGB input (no HDR tone map)',
                      clipped_output_pixels_percent=100 * clipped / (6 * size * size),
                      prepared_faces=[dict(path=str(p), sha256=core.sha(p)) for p in faces])


def set_marker(model):
    """Only touch exported JSON; preserve existing IDs, hierarchy and source scene."""
    meshes = model.setdefault('meshes', [])
    markers = [n for n in meshes if 'hasskybox' in n.get('name', '').lower()]
    if len(markers) > 1:
        raise ValueError('Several exported skybox markers: retain exactly one before skybox export')
    if markers:
        if markers[0].get('indices') or markers[0].get('positions'):
            raise ValueError('Skybox marker must be an empty node, not visible geometry')
        markers[0]['name'] = 'hasskyboxbasis'
        return 'updated existing marker'
    ids = {n.get('id') for key in ('meshes', 'transformNodes', 'lights', 'cameras') for n in model.get(key, [])}
    node_id, suffix = 'hasskyboxbasis', 1
    while node_id in ids:
        node_id = 'hasskyboxbasis_' + str(suffix)
        suffix += 1
    meshes.append(dict(name='hasskyboxbasis', id=node_id, position=[0,0,0],
                       rotation=[0,0,0], scaling=[1,1,1], isVisible=False, isEnabled=True))
    return 'added delivery-only marker'


def export_skybox(context, model, work, package, options):
    import bpy
    from .environment_controls import find_controls, visible_panorama
    tool, version = find_basisu(options.get('executable', ''))
    if not tool:
        raise ValueError(version)
    managed = options.get('world_controls', True) and find_controls(context.scene.world)
    path = world_panorama(context) if managed else options.get('image') or world_panorama(context)
    if not path:
        raise ValueError('Select a saved skybox panorama; no unique connected World image was found')
    source = Path(bpy.path.abspath(path)).resolve()
    if not source.is_file():
        raise ValueError('Skybox source does not exist: ' + str(source))
    marker_change = set_marker(model)
    directory = work / 'skybox'
    if managed:
        from .environment_controls import settings
        source = Path(settings(managed)['image']).resolve()
        original_hash = core.sha(source)
        with visible_panorama(context, directory) as (temporary, values):
            faces, report = prepare_faces(temporary, directory / 'faces', int(options.get('size', 1024)),
                360 + float(options.get('world_rotation_offset', 0)), float(options.get('exposure', 0)),
                options.get('tone_map', 'REINHARD'))
            report.update(evaluated_panorama_sha256=report['source_sha256'],
                          source=str(source), source_sha256=original_hash,
                          world_controls=values, temporary_panorama_removed=True)
    else:
        faces, report = prepare_faces(source, directory / 'faces', int(options.get('size', 1024)),
                                      float(options.get('rotation', 180)), float(options.get('exposure', 0)),
                                      options.get('tone_map', 'REINHARD'))
    output = directory / 'cubemap.basis'
    threads = int(options.get('threads', 8))
    quality = int(options.get('quality', 255))
    if not 1 <= quality <= 255 or not 1 <= threads <= 128:
        raise ValueError('Invalid skybox encoder quality/thread count')
    command = [tool, '-basis', '-etc1s', '-tex_array', '-tex_type', 'cubemap', '-srgb',
               '-q', str(quality), '-comp_level', '2', '-max_threads', str(threads),
               '-output_file', str(output)]
    for face in faces:
        command += ['-file', str(face)]
    report.update(basisu=version, command=command, codec='ETC1S', quality=quality,
                  compression_level=2, mip_levels=1, alpha=False, color_space='srgb',
                  marker_change=marker_change)
    (directory / 'settings.json').write_text(json.dumps(report, indent=2), encoding='utf8')
    proc = subprocess.run(command, capture_output=True, text=True, **process_options())
    (directory / 'encode.log').write_text(proc.stdout + proc.stderr, encoding='utf8')
    if proc.returncode or not output.is_file():
        raise RuntimeError('Basis skybox encoding failed; see ' + str(directory / 'encode.log'))
    check = subprocess.run([tool, '-validate', str(output)], capture_output=True, text=True, **process_options())
    (directory / 'validate.log').write_text(check.stdout + check.stderr, encoding='utf8')
    info = subprocess.run([tool, '-info', str(output)], capture_output=True, text=True, **process_options())
    (directory / 'info.log').write_text(info.stdout + info.stderr, encoding='utf8')
    images = re.findall(r'Image \d+: MipLevels: (\d+) OrigDim: (\d+)x(\d+)', info.stdout)
    expected = ('1', str(report['face_size']), str(report['face_size']))
    if check.returncode or info.returncode or len(images) != 6 or any(i != expected for i in images) or 'Texture format: ETC1S' not in info.stdout or 'Texture type: cubemap array' not in info.stdout:
        raise RuntimeError('Basis skybox validation failed; see ' + str(directory))
    if core.sha(source) != report['source_sha256']:
        raise RuntimeError('Skybox source changed during encoding')
    if (package / 'cubemap.basis').exists():
        raise ValueError('Skybox output collides with an existing packaged texture: cubemap.basis')
    shutil.copy2(output, package / 'cubemap.basis')
    report.update(status='passed', output='cubemap.basis', bytes=output.stat().st_size, sha256=core.sha(output))
    return report
