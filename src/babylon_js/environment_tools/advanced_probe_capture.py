"""Six native perspective views, matched ENV sampling and resumable measurements."""
import json
import math
import os
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory


def advanced_options(config):
    """Schema 1 always keeps its historical panorama-hemisphere behavior."""
    version = config.get('schema_version')
    if type(version) is not int or version not in (1, 2):
        raise ValueError('Expected volume capture schema_version 1 or 2')
    include = config.get('include_advanced', True)
    if type(include) is not bool:
        raise ValueError('include_advanced must be a boolean')
    if version == 1 or not include:
        return include, None
    options = config.get('advanced', {})
    if not isinstance(options, dict) or options.keys() - {
        'fog_per_metre', 'source_multiplier', 'samples', 'material_roles'
    }:
        raise ValueError('Invalid Advanced capture options')
    fog = options.get('fog_per_metre', .25)
    multiplier = options.get('source_multiplier', 4.)
    samples = options.get('samples', 8)
    for value, label in ((fog, 'fog_per_metre'), (multiplier, 'source_multiplier')):
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError('Advanced ' + label + ' must be finite and nonnegative')
    if multiplier == 0:
        raise ValueError('Advanced source_multiplier must be positive')
    if type(samples) is not int or not 1 <= samples <= 64:
        raise ValueError('Advanced samples must be in 1..64')
    roles = options.get('material_roles', {})
    if not isinstance(roles, dict):
        raise ValueError('Advanced material_roles maps object names to slot indices and roles')
    for name, slots in roles.items():
        if not isinstance(name, str) or not name or not isinstance(slots, dict) or not slots:
            raise ValueError('Advanced material_roles requires a nonempty object and slot mapping')
        for index, role in slots.items():
            if (not isinstance(index, str) or not index.isascii() or not index.isdecimal() or
                    str(int(index)) != index or role not in ('emitter', 'baked-display')):
                raise ValueError('Advanced material roles must use canonical slot indices and emitter or baked-display')
    return include, dict(fog_per_metre=float(fog), source_multiplier=float(multiplier),
                         samples=samples, material_roles=roles)


def camera_rays(size, fov_degrees):
    import numpy as np
    centres = 2 * (np.arange(size) + .5) / size - 1
    xx, yy = np.meshgrid(centres, -centres)
    tangent = math.tan(math.radians(fov_degrees) / 2)
    rays = np.stack((xx * tangent, yy * tangent, -np.ones_like(xx)), axis=-1)
    return rays, np.linalg.norm(rays, axis=-1)


def camera_rotations():
    from mathutils import Vector
    # Babylon environment XYZ -> Blender XZY.
    return [Vector(axis).to_track_quat('-Z', 'Y') for axis in (
        (1, 0, 0), (-1, 0, 0), (0, 0, 1), (0, 0, -1), (0, 1, 0), (0, -1, 0))]


def rectilinear_reference(panorama, rays, rotations):
    import numpy as np
    rgb = np.asarray(panorama, dtype=np.float64)[..., :3]
    if (rgb.ndim != 3 or rgb.shape[2] != 3 or not rgb.size or
            rgb.shape[1] != rgb.shape[0] * 2 or not np.isfinite(rgb).all() or (rgb < 0).any()):
        raise ValueError('Advanced reference must be a complete, nonnegative linear RGB panorama')
    height, width = rgb.shape[:2]
    rays = rays / np.linalg.norm(rays, axis=-1)[..., None]
    result = []
    for rotation in rotations:
        directions = rays @ np.asarray(rotation, dtype=np.float64).T
        x, y, z = directions[..., 0], directions[..., 2], directions[..., 1]
        u = (np.arctan2(x, z) + math.pi) / (2 * math.pi) * width - .5
        v = (.5 - np.arcsin(np.clip(y, -1, 1)) / math.pi) * height - .5
        col = np.floor(u).astype(int)
        row = np.floor(v).astype(int)
        fx, fy = (u - col)[..., None], (v - row)[..., None]
        left, right = col % width, (col + 1) % width
        top, bottom = row.clip(0, height - 1), (row + 1).clip(0, height - 1)
        samples = ((1 - fy) * ((1 - fx) * rgb[top, left] + fx * rgb[top, right]) +
                   fy * ((1 - fx) * rgb[bottom, left] + fx * rgb[bottom, right]))
        result.append(samples.mean(axis=(0, 1)))
    reference = np.asarray(result)
    if reference.shape != (6, 3) or not np.isfinite(reference).all() or (reference <= 1e-6).any():
        raise ValueError('Advanced rectilinear ENV reference contains zero/near-zero RGB')
    return reference


def fogged_mean(rgb, axial_depth, ray_length, fog_per_metre, metres_per_unit):
    import numpy as np
    rgb = np.asarray(rgb, dtype=np.float64)
    depth = np.asarray(axial_depth, dtype=np.float64)
    if rgb.shape != (*ray_length.shape, 3) or depth.shape != ray_length.shape:
        raise ValueError('Combined and Z must match the calibrated perspective resolution')
    if not np.isfinite(rgb).all() or (rgb < 0).any() or np.isnan(depth).any() or (depth < 0).any():
        raise ValueError('Advanced perspective requires finite nonnegative RGB and nonnegative Z')
    if fog_per_metre == 0:
        return rgb.mean(axis=(0, 1))
    # Positive infinite or very large background Z goes black with nonzero fog.
    with np.errstate(over='ignore'):
        weight = np.exp(-fog_per_metre * metres_per_unit * depth * ray_length)
    return (rgb * weight[..., None]).mean(axis=(0, 1))


def relative_directions(measured, reference):
    import numpy as np
    rgb = np.asarray(measured, dtype=np.float64)
    if rgb.shape != (6, 3) or not np.isfinite(rgb).all() or (rgb < 0).any():
        raise ValueError('Advanced checkpoint must contain six finite nonnegative measured RGB rows')
    ratio = rgb / reference
    if not np.isfinite(ratio).all() or (ratio > 255).any():
        raise ValueError('Advanced perspective gain exceeds RGBD range; do not clip or normalize it')
    return ratio.astype(np.float32)


def read_combined_and_z(path, layer):
    """Read named float passes whether OpenEXR puts them in one part or several."""
    import OpenImageIO as oiio
    reader = oiio.ImageInput.open(str(path))
    if reader is None:
        raise ValueError('Cannot open perspective render passes: ' + str(path))
    matches = {'Combined': [], 'Depth': []}
    try:
        part = 0
        while reader.seek_subimage(part, 0):
            names = list(reader.spec().channelnames)
            pixels = None
            for name, components in (('Combined', 'RGB'), ('Depth', 'Z')):
                indices = []
                for component in components:
                    suffix = f'{name}.{component}'
                    index = next((i for i, channel in enumerate(names)
                                  if channel in (suffix, f'{layer}.{suffix}')), None)
                    indices.append(index)
                if all(index is not None for index in indices):
                    if pixels is None:
                        pixels = reader.read_image(format=oiio.FLOAT)
                    if pixels is None:
                        raise ValueError('Cannot read perspective render pixels: ' + reader.geterror())
                    matches[name].append(pixels[..., indices])
            part += 1
    finally:
        reader.close()
    if any(len(passes) != 1 for passes in matches.values()):
        raise ValueError('Expected one Combined RGB and one Depth Z pass for ' + layer)
    return matches['Combined'][0], matches['Depth'][0][..., 0]


def perspective_profile(scene, layer, options):
    from babylon_js.environment_lighting_volume import (
        ADVANCED_PERSPECTIVE_METHOD, ADVANCED_PERSPECTIVE_SIZE, ADVANCED_PERSPECTIVE_FOV,
    )
    scale = float(scene.unit_settings.scale_length)
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError('Advanced capture requires a positive scene metres-per-Blender-unit scale')
    return dict(method=ADVANCED_PERSPECTIVE_METHOD, resolution=[ADVANCED_PERSPECTIVE_SIZE] * 2,
                fovDegrees=[ADVANCED_PERSPECTIVE_FOV] * 2, samples=options['samples'],
                blackFogPerMetre=options['fog_per_metre'], sourceMultiplier=options['source_multiplier'],
                metresPerBlenderUnit=scale, viewLayer=layer.name,
                depth='cycles-axial-z-times-pixel-centre-ray-length')


@contextmanager
def perspective_renderer(scene, layer, profile, panorama, directory):
    import bpy
    from spatial_capture import set_exr_format
    size = profile['resolution'][0]
    rays, ray_length = camera_rays(size, profile['fovDegrees'][0])
    rotations = camera_rotations()
    reference = rectilinear_reference(panorama, rays, [r.to_matrix() for r in rotations])
    render = scene.render
    camera = scene.camera
    image = render.image_settings
    states = (
        (render, ('resolution_x', 'resolution_y', 'resolution_percentage', 'pixel_aspect_x',
                  'pixel_aspect_y', 'use_border', 'use_multiview', 'filepath')),
        (scene.cycles, ('samples',)),
        (layer, ('use_pass_z', 'use_pass_combined', 'samples', 'world_override', 'material_override')),
        (image, ('media_type', 'file_format', 'color_mode', 'color_depth', 'exr_codec')),
        (camera.data, ('type', 'lens_unit', 'lens', 'sensor_fit', 'shift_x', 'shift_y')),
        (camera, ('rotation_mode', 'rotation_quaternion', 'location')),
    )
    saved = []
    for owner, keys in states:
        fields = []
        for key in keys:
            if hasattr(owner, key):
                value = getattr(owner, key)
                fields.append((key, value.copy() if key in ('rotation_quaternion', 'location') else value))
        saved.append((owner, fields))
    try:
        render.resolution_x = render.resolution_y = size
        render.resolution_percentage = 100
        render.pixel_aspect_x = render.pixel_aspect_y = 1
        render.use_border = render.use_multiview = False
        camera.data.type = 'PERSP'
        camera.data.sensor_fit = 'HORIZONTAL'
        camera.data.lens_unit = 'FOV'
        camera.data.angle = math.radians(profile['fovDegrees'][0])
        camera.data.shift_x = camera.data.shift_y = 0
        camera.rotation_mode = 'QUATERNION'
        scene.cycles.samples = profile['samples']
        # Source classification and ENV matching refer to the prepared
        # scene's materials and World, even if a layer has preview overrides.
        layer.material_override = layer.world_override = None
        layer.use_pass_z = layer.use_pass_combined = True
        layer.samples = 0
        set_exr_format(scene, multilayer=True)
        image.color_mode = 'RGBA'
        with TemporaryDirectory(prefix='perspective-', dir=directory) as temp:
            path = Path(temp) / 'view.exr'
            render.filepath = str(path)

            def render_position(position):
                camera.location = (position[0], position[2], position[1])
                means = []
                for rotation in rotations:
                    camera.rotation_quaternion = rotation
                    path.unlink(missing_ok=True)
                    if 'FINISHED' not in bpy.ops.render.render(layer=layer.name, write_still=True):
                        raise RuntimeError('Advanced perspective render did not finish')
                    rgb, depth = read_combined_and_z(path, layer.name)
                    means.append(fogged_mean(rgb, depth, ray_length, profile['blackFogPerMetre'],
                                            profile['metresPerBlenderUnit']).tolist())
                return means

            yield reference, render_position
    finally:
        for owner, fields in saved:
            for key, value in fields:
                setattr(owner, key, value)


def measure_position(position, path, input_sha256, reference, render):
    from common import save_json
    position = list(position)
    if path.is_file():
        record = json.loads(path.read_text(encoding='utf8'))
        if record.get('position') != position or record.get('captureInputsSha256') != input_sha256:
            raise ValueError('Advanced probe checkpoint position or capture inputs changed: ' + str(path))
        ratio = relative_directions(record.get('measuredRgb'), reference)
        if record.get('ratioRgb') != ratio.tolist():
            raise ValueError('Advanced probe checkpoint RGB ratios changed: ' + str(path))
        return ratio, record
    measured = render(position)
    ratio = relative_directions(measured, reference)
    record = dict(position=position, captureInputsSha256=input_sha256,
                  measuredRgb=measured, ratioRgb=ratio.tolist())
    save_json(path, record)
    return ratio, record


@contextmanager
def exclusive_capture(output):
    """Hold the new capture directory while writing trial/grid checkpoints."""
    with (output / '.capture.lock').open('a+b') as file:
        try:
            if os.name == 'nt':
                import msvcrt
                file.seek(0)
                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise ValueError('Another capture is writing this output: ' + str(output)) from error
        yield
