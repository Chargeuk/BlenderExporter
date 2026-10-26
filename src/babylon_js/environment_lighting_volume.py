"""Opt-in RGBD environment-lighting volume, independent of analytic lights."""
import math
from pathlib import Path

KEY = 'kadshowEnvironmentLightingVolume'
FILENAME = 'environment_lighting_volume_rgbd.ktx2'
DIMENSIONS = [32, 16, 32]
LAYOUT = 'y-slices-row-major-xz'
ADVANCED_FILENAME = 'environment_lighting_volume_advanced_rgbd.ktx2'
ADVANCED_LAYOUT = 'ambient-cube-6-y-slices-row-major-xz'
DIRECTION_ORDER = ['+X', '-X', '+Y', '-Y', '+Z', '-Z']
PANORAMA_CONVENTION = 'equirectangular-center-babylon-positive-z-right-positive-x-up-positive-y-v1'


def saved_options(world):
    """A saved association is the opt-in; absent config keeps exports unchanged."""
    config = world.get('bjs_environment_lighting_volume_config', '')
    if not config: return None
    result = dict(config=config, image=world.get('bjs_environment_lighting_volume_image', ''),
        strength=world.get('bjs_environment_lighting_volume_strength', 1))
    simple_mode = world.get('bjs_environment_lighting_volume_simple_divisor_mode')
    if simple_mode is not None: result['simple_divisor_mode'] = simple_mode
    policy = world.get('bjs_environment_lighting_volume_advanced_alpha_policy')
    if policy is not None: result['advanced_alpha_policy'] = policy
    return result


def marker_metadata(model):
    """Mesh metadata survives KaDshow's custom asset-container import path."""
    markers = [mesh for mesh in model.get('meshes', []) if mesh.get('name', '').lower() == 'hasenv']
    if len(markers) != 1 or markers[0].get('positions') or markers[0].get('indices'):
        raise ValueError('Environment lighting volume requires one existing empty hasenv mesh marker; export ENV first')
    metadata = markers[0].setdefault('metadata', {})
    if not isinstance(metadata, dict) or KEY in metadata:
        raise ValueError('Invalid or duplicate hasenv environment lighting volume metadata')
    return metadata


def panorama_mean(pixels):
    """RGB spherical mean using exact equirectangular row solid angles."""
    import numpy as np
    rgb = np.asarray(pixels, dtype=np.float64)[..., :3]
    if rgb.ndim != 3 or rgb.shape[2] != 3 or not np.isfinite(rgb).all() or (rgb < 0).any():
        raise ValueError('Panorama must contain finite nonnegative linear RGB')
    height, width = rgb.shape[:2]
    if width != height * 2:
        raise ValueError('Expected a complete 2:1 equirectangular panorama')
    edges = np.linspace(0, math.pi, height + 1)
    weights = np.cos(edges[:-1]) - np.cos(edges[1:])
    return (rgb * weights[:, None, None]).sum(axis=(0, 1)) / (weights.sum() * width)


def relative_rgb(pixels, reference_rgb, epsilon=1e-6):
    import numpy as np
    reference = np.asarray(reference_rgb, dtype=np.float64)
    if reference.shape != (3,) or not np.isfinite(reference).all() or (reference <= epsilon).any():
        raise ValueError('ENV reference RGB is zero/near-zero; no stable per-channel ratio')
    ratio = panorama_mean(pixels) / reference
    if not np.isfinite(ratio).all() or (ratio > 255).any():
        raise ValueError('Probe RGB ratio exceeds RGBD range; do not clip or locally normalize')
    return ratio.astype(np.float32)


def panorama_directions(height, width):
    """Canonical camera X rotation pi/2; top-left EXR, Babylon XZY axes.

    The native six-wall calibration must verify all signs before reusing older
    captures. Pixel centres represent the complete sphere, including the seam.
    """
    import numpy as np
    longitude = (np.arange(width) + .5) * (2 * math.pi / width) - math.pi
    latitude = math.pi/2 - (np.arange(height) + .5) * (math.pi / height)
    directions = np.empty((height, width, 3), np.float64)
    directions[..., 0] = np.cos(latitude)[:, None] * np.sin(longitude)[None, :]
    directions[..., 1] = np.sin(latitude)[:, None]
    directions[..., 2] = np.cos(latitude)[:, None] * np.cos(longitude)[None, :]
    return directions


def hemisphere_means(pixels):
    """Six cosine-hemisphere RGB means, with equirectangular solid angles."""
    import numpy as np
    rgb = np.asarray(pixels, dtype=np.float64)[..., :3]
    if rgb.ndim != 3 or rgb.shape[2] != 3 or not np.isfinite(rgb).all() or (rgb < 0).any():
        raise ValueError('Hemisphere measurement requires finite nonnegative RGB')
    height, width = rgb.shape[:2]
    if width != height * 2: raise ValueError('Expected a complete 2:1 panorama')
    directions = panorama_directions(height, width)
    edges = np.linspace(0, math.pi, height + 1)
    solid_angle = (np.cos(edges[:-1]) - np.cos(edges[1:]))[:, None]
    result = []
    for axis in range(3):
        for sign in (1, -1):
            weights = np.maximum(sign * directions[..., axis], 0) * solid_angle
            result.append((rgb * weights[..., None]).sum(axis=(0, 1)) / weights.sum())
    return np.asarray(result)


def relative_hemispheres(pixels, reference_rgb, epsilon=1e-6):
    import numpy as np
    reference = np.asarray(reference_rgb, dtype=np.float64)
    if reference.shape != (6, 3) or not np.isfinite(reference).all() or (reference <= epsilon).any():
        raise ValueError('ENV hemisphere reference RGB is zero/near-zero')
    ratio = hemisphere_means(pixels) / reference
    if not np.isfinite(ratio).all() or (ratio > 255).any():
        raise ValueError('Directional gain exceeds RGBD range; do not clip or normalize it')
    return ratio.astype(np.float32)


def pack_advanced_atlas(volumes):
    import numpy as np
    if np.shape(volumes) != (6, 32, 16, 32, 4):
        raise ValueError('Advanced volume requires six [z,y,x,rgba] banks')
    return np.concatenate([pack_atlas(volume) for volume in volumes], axis=0)


def resample_grid(grid, dimensions=DIMENSIONS):
    """Trilinear texel-centre interpolation; memory order is [z,y,x,rgba]."""
    import numpy as np
    result = np.asarray(grid, dtype=np.float32)
    if result.ndim != 4 or result.shape[-1] != 4 or not np.isfinite(result).all():
        raise ValueError('Volume must be finite [z,y,x,rgba] data')
    for axis, count in enumerate(reversed(dimensions)):
        source = result.shape[axis]
        coordinate = np.clip((np.arange(count) + .5) * source / count - .5, 0, source - 1)
        lower = coordinate.astype(int); upper = np.minimum(lower + 1, source - 1)
        shape = [1] * 4; shape[axis] = count
        fraction = (coordinate - lower).reshape(shape)
        result = np.take(result, lower, axis=axis) * (1 - fraction) + np.take(result, upper, axis=axis) * fraction
    result[..., 3] = 1
    return np.ascontiguousarray(result, dtype=np.float32)


def pack_atlas(volume):
    """Top-left atlas: Y slices row-major; each tile X right and Z down."""
    import numpy as np
    array = np.asarray(volume, dtype=np.float32)
    nx, ny, nz = DIMENSIONS
    if array.shape != (nz, ny, nx, 4) or not np.isfinite(array).all() or (array[..., :3] < 0).any():
        raise ValueError('Expected finite nonnegative 32x16x32 RGBA volume')
    atlas = np.empty((nz * 4, nx * 4, 4), np.float32)
    for y in range(ny):
        row, column = divmod(y, 4)
        atlas[row*nz:(row+1)*nz, column*nx:(column+1)*nx] = array[:, y]
    return atlas


def validate_record(record):
    from .spatial_lighting import finite
    if 'pathBase' in record and record['pathBase'] != 'receipt':
        raise ValueError('Unsupported volume receipt path base')
    if type(record.get('version')) is not int or record['version'] != 1:
        raise ValueError('Environment lighting volume receipt version must be 1')
    if record.get('dimensions') != DIMENSIONS or record.get('atlasColumns') != 4 or record.get('atlasRows') != 4 or record.get('atlasLayout') != LAYOUT:
        raise ValueError('Unsupported environment lighting volume dimensions/layout')
    bounds = record.get('bounds', {})
    for key in ('min', 'max'):
        if len(bounds.get(key, [])) != 3:
            raise ValueError('Volume bounds require Babylon environment-local XYZ min/max')
        for value in bounds[key]: finite(value, 'volume bounds')
    if any(a >= b for a, b in zip(bounds['min'], bounds['max'])):
        raise ValueError('Volume bounds must have positive extent')
    if record.get('normalization', {}).get('method') != 'solid-angle-rgb-mean-over-env-reference':
        raise ValueError('Volume must use the matching ENV RGB reference')
    reference = record['normalization'].get('referenceRgb', [])
    if len(reference) != 3:
        raise ValueError('Missing measured ENV reference RGB')
    for value in reference: finite(value, 'reference RGB', 1e-6)
    if record.get('flip_y') is not False:
        raise ValueError('Volume atlas requires flip_y=false')
    digest = record.get('masterSha256', '')
    if len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
        raise ValueError('Missing volume atlas SHA256')
    if 'advanced' in record: validate_advanced(record['advanced'], record)
    return record


def validate_advanced(advanced, simple):
    from .spatial_lighting import finite
    if not isinstance(advanced, dict): raise ValueError('Advanced volume record must be an object')
    if type(advanced.get('version')) is not int or advanced['version'] != 1:
        raise ValueError('Advanced volume version must be 1')
    if advanced.get('dimensions') != DIMENSIONS or advanced.get('bounds') != simple['bounds']:
        raise ValueError('Advanced volume bounds/dimensions must match Simple')
    if advanced.get('directionOrder') != DIRECTION_ORDER or advanced.get('atlasColumns') != 4 or advanced.get('atlasRows') != 24 or advanced.get('atlasLayout') != ADVANCED_LAYOUT:
        raise ValueError('Unsupported Advanced volume direction/atlas layout')
    if advanced.get('flip_y') is not False or advanced.get('panoramaConvention') != PANORAMA_CONVENTION:
        raise ValueError('Advanced volume panorama/atlas orientation is unverified')
    norm = advanced.get('normalization', {})
    if norm.get('method') != 'cosine-hemisphere-rgb-mean-over-env-reference' or len(norm.get('referenceRgb', [])) != 6:
        raise ValueError('Advanced volume requires six ENV hemisphere RGB references')
    for rgb in norm['referenceRgb']:
        if len(rgb) != 3: raise ValueError('Expected RGB hemisphere reference')
        for value in rgb: finite(value, 'hemisphere RGB', 1e-6)
    for key in ('masterSha256', 'provenanceSha256'):
        digest = advanced.get(key, '')
        if len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
            raise ValueError('Missing Advanced volume hash: ' + key)
    for key in ('master', 'provenance'):
        filename = advanced.get(key, '')
        if not filename or Path(filename).name != filename:
            raise ValueError('Advanced volume files must be beside the receipt')
    return advanced


def advanced_payload(record, texture_sha256, min_divisor):
    from .spatial_lighting import finite
    return dict(version=1, texture=ADVANCED_FILENAME, textureSha256=texture_sha256,
        encoding='rgbd-v1', minDivisor=finite(min_divisor, 'Advanced minDivisor', 1/255, 1),
        strength=1, coordinateSpace='environment-local', outsideBounds='neutral',
        bounds=record['bounds'], dimensions=list(DIMENSIONS), directionOrder=list(DIRECTION_ORDER),
        atlasColumns=4, atlasRows=24, atlasLayout=ADVANCED_LAYOUT)


def payload(record, texture_sha256, min_divisor, strength=1):
    from .spatial_lighting import finite
    validate_record(record)
    if len(texture_sha256) != 64 or any(c not in '0123456789abcdef' for c in texture_sha256):
        raise ValueError('Invalid delivered volume texture SHA256')
    return dict(version=1, texture=FILENAME, textureSha256=texture_sha256,
        encoding='rgbd-v1', minDivisor=finite(min_divisor, 'minDivisor', 1/255, 1),
        strength=finite(strength, 'volume strength', 0, 100), coordinateSpace='environment-local',
        bounds=record['bounds'], dimensions=list(DIMENSIONS), atlasColumns=4, atlasRows=4,
        atlasLayout=LAYOUT, outsideBounds='neutral', neutralRgb=[1, 1, 1],
        sampling='texel-centered-trilinear', normalization=record['normalization'])


def validate_source(context, record, environment_options=None, receipt_path=None):
    import bpy
    import sys
    from . import ktx_conversion as core, environment_controls
    sys.path.insert(0, str(Path(__file__).parent / 'environment_tools'))
    from .environment_tools.appearance import preview_identity, verify_combination
    from .environment_tools.ao import geometry_identity
    from .environment_tools.volume_relocation import package_paths, validate_relocation
    source = package_paths(record, receipt_path)
    validate_relocation(context, record, receipt_path)
    for key in ('scene', 'scopeRecipe', 'combined', 'combinedProvenance', 'reference'):
        path = Path(source[key])
        if not path.is_file() or core.sha(path) != source[key + 'Sha256']:
            raise ValueError('Volume capture input changed: ' + key)
    material = bpy.data.materials.get(context.scene.get('bjs_lighting_controls_material', ''))
    if material is None or preview_identity(material) != source['previewSha256']:
        raise ValueError('Volume saved appearance graph/source images changed')
    verify_combination(material, source['combined'], source['combinedProvenance'])
    if geometry_identity(core.read_json(Path(source['scopeRecipe'])))['geometry_sha256'] != source['geometrySha256']:
        raise ValueError('Volume receiver geometry/UV/transforms changed')
    current = environment_controls.settings(environment_controls.find_controls(context.scene.world))
    for key in ('Rotation', 'Lighting Strength', 'Sky Lift', 'Lift Rolloff'):
        if abs(current[key] - source['worldControls'][key]) > 1e-5:
            raise ValueError('Volume World Lighting changed: ' + key)
    if core.sha(Path(current['image'])) != source['worldImageSha256']:
        raise ValueError('Volume World image changed')
    associated = context.scene.world.get('bjs_environment_image', '')
    if not associated or Path(bpy.path.abspath(associated)).resolve() != Path(source['reference']).resolve():
        raise ValueError('Volume reference differs from saved ENV source')
    if environment_options:
        if Path(bpy.path.abspath(environment_options.get('image', associated))).resolve() != Path(source['reference']).resolve():
            raise ValueError('Exported ENV differs from volume reference')
        if any(environment_options.get(k, default) != default for k, default in (('exposure', 0), ('highlight_gain', 1), ('highlight_threshold', 1))):
            raise ValueError('Volume requires the accepted neutral ENV conversion settings')


def read_prepared_volume_rgba(prepared):
    """Read RGBD data bytes without associating RGB with the divisor alpha."""
    import numpy as np
    import OpenImageIO as oiio
    prepared = Path(prepared)
    try:
        header = prepared.read_bytes()[:29]
    except OSError as error:
        raise ValueError('Cannot read volume prepared PNG: ' + str(prepared)) from error
    if len(header) < 29 or header[:8] != b'\x89PNG\r\n\x1a\n' or header[24:26] != bytes((8, 6)):
        raise ValueError('Volume preparation requires actual PNG RGBA8')
    config = oiio.ImageSpec()
    config.attribute('oiio:UnassociatedAlpha', 1)
    config.attribute('oiio:reorient', 0)
    image = oiio.ImageBuf(str(prepared), 0, 0, config)
    pixels = image.get_pixels(oiio.UINT8)
    if pixels is None or str(image.spec().format) != 'uint8' or pixels.ndim != 3 or pixels.shape[2] != 4 or not pixels.size:
        raise ValueError('Cannot decode volume prepared PNG as RGBA8: ' + image.geterror())
    if pixels.dtype != np.uint8:
        raise ValueError('Volume prepared PNG must retain uint8 data bytes')
    return pixels


def verify_prepared_volume_rgba(master, actual, divisor_mode='per-pixel', *, fixed_divisor=False):
    """Pure byte guard shared by producer tests and the actual pre-encoder boundary."""
    import hashlib
    import numpy as np
    from . import ktx_conversion as core
    expected, _ = core.encode_rgbd(master[..., :3], divisor_mode, fixed_divisor=fixed_divisor)
    if actual is None or actual.dtype != np.uint8 or not np.array_equal(actual, expected):
        raise ValueError('Volume prepared PNG differs from whole original float-master RGBD encoding')
    return hashlib.sha256(actual.tobytes()).hexdigest()


def _export_texture(source, source_sha256, filename, dimensions, work, package, ktx, threads, *, fixed_divisor=False, divisor_mode='per-pixel', advanced_alpha_policy=None):
    import subprocess
    from . import ktx_conversion as core
    from .ktx_export import prepare_blender, process_options
    if source.suffix.lower() != '.exr' or not source.is_file() or core.sha(source) != source_sha256:
        raise ValueError('Volume atlas missing or differs from capture receipt')
    if advanced_alpha_policy not in (None, 'source-ceiling'):
        raise ValueError('Unsupported Advanced alpha policy: ' + str(advanced_alpha_policy))
    if advanced_alpha_policy is not None:
        import os, struct
        if not fixed_divisor: raise ValueError('Source-ceiling requires fixed-divisor Advanced')
        if os.name != 'nt' or struct.calcsize('P') != 8:
            raise ValueError('Source-ceiling requires verified Windows64 libktx')
        if not (Path(ktx).resolve().parent / 'ktx.dll').is_file():
            raise ValueError('Source-ceiling requires libktx beside the provided KTX executable')
    target = package / filename
    if target.exists(): raise ValueError('Volume texture collides with existing package asset')
    stage = work / filename.removesuffix('_rgbd.ktx2'); stage.mkdir()
    prepared, metrics = prepare_blender(dict(source_path=str(source), output=filename,
        encoding='rgbd-v1', rgbd_fixed_divisor=fixed_divisor, rgbd_divisor_mode=divisor_mode,
        size=0, flip_y=False, color_space='linear', codec='uastc', mipmaps=False, alpha='preserve'), stage)
    if fixed_divisor: metrics['rgbd_divisor_mode'] = 'global-fixed'
    elif divisor_mode == 'block-4x4': metrics['rgbd_divisor_mode'] = divisor_mode
    if metrics['dimensions'] != dimensions: raise ValueError('Volume atlas dimensions differ from contract')
    # Prove the actual prepared PNG against encoding the unchanged whole float master BEFORE KTX create.
    from .agx_lightmap import read_exr
    actual_pixels = read_prepared_volume_rgba(prepared)
    rgba_hash = verify_prepared_volume_rgba(read_exr(source), actual_pixels, divisor_mode, fixed_divisor=fixed_divisor)
    metrics['source_preparation_guard'] = dict(when='before-ktx-create', byte_exact=True,
        source_sha256=source_sha256, prepared_sha256=core.sha(prepared),
        rgba_sha256=rgba_hash,
        divisor_mode='global-fixed' if fixed_divisor else divisor_mode)
    # Keep quality4's unconstrained original for diagnosis. The explicit policy
    # caps endpoint alpha only; D and prepared numerator remain source-derived.
    encoded = stage / (target.stem + '_unconstrained.ktx2') if advanced_alpha_policy else target
    if encoded.exists(): raise ValueError('Unconstrained KTX path must be fresh')
    if advanced_alpha_policy and (metrics['alpha_min'] != metrics['alpha_max'] or not 1 <= metrics['alpha_min'] <= 255):
        raise ValueError('Advanced prepared alpha must be one original divisor byte')
    if advanced_alpha_policy:
        from .uastc_alpha import read_fixed_preparation
        read_fixed_preparation(prepared, int(metrics['alpha_min']))
    command = [ktx, 'create', '--format', 'R8G8B8A8_UNORM', '--assign-oetf', 'linear',
        '--encode', 'uastc', '--threads', str(threads), '--uastc-quality', '4', '--zstd', '3', str(prepared), str(encoded)]
    for argv in (command, [ktx, 'validate', str(encoded)]):
        run = subprocess.run(argv, capture_output=True, text=True, **process_options())
        if run.returncode: raise RuntimeError('Volume KTX conversion failed: ' + run.stdout + run.stderr)
    alpha_proof = None
    if advanced_alpha_policy:
        from .uastc_alpha import enforce_source_ceiling
        alpha_proof = enforce_source_ceiling(encoded, target, prepared=prepared,
            divisor=int(metrics['alpha_min']), ktx=ktx)
    if core.sha(source) != source_sha256: raise ValueError('Volume master changed during conversion')
    digest = core.sha(target)
    result = dict(status='passed', texture=filename, textureSha256=digest, bytes=target.stat().st_size,
        source=str(source), metrics=metrics, command=command)
    if alpha_proof is not None:
        result['advancedAlphaPolicy'] = alpha_proof
    return result


def export_volume(context, model, work, package, options, ktx, threads=8, environment_options=None):
    """Old receipts export Simple only; newer receipts also export Advanced."""
    import bpy
    from . import ktx_conversion as core
    alpha_policy = options.get('advanced_alpha_policy')
    if alpha_policy not in (None, 'source-ceiling'):
        raise ValueError('Unsupported Advanced alpha policy: ' + str(alpha_policy))
    simple_mode = options.get('simple_divisor_mode', 'per-pixel')
    if simple_mode not in ('per-pixel', 'block-4x4'):
        raise ValueError('Unsupported Simple volume divisor mode: ' + str(simple_mode))
    if context.scene.world.preserveZUpRight:
        raise ValueError('Environment lighting volume requires Babylon Y-up export')
    metadata = marker_metadata(model)
    receipt = Path(bpy.path.abspath(options['config'])).resolve()
    record = validate_record(core.read_json(receipt))
    if alpha_policy is not None and 'advanced' not in record:
        raise ValueError('Advanced alpha policy requires an Advanced receipt')
    validate_source(context, record, environment_options, receipt)
    source = Path(bpy.path.abspath(options['image'])).resolve() if options.get('image') else (receipt.parent / record['master']).resolve()
    simple_kwargs = {'divisor_mode': simple_mode} if simple_mode != 'per-pixel' else {}
    report = _export_texture(source, record['masterSha256'], FILENAME, [128,128], work, package, ktx, threads, **simple_kwargs)
    report['receipt'] = str(receipt)
    descriptor = payload(record, report['textureSha256'], report['metrics']['alpha_min']/255, options.get('strength',1))
    if 'advanced' in record:
        advanced = record['advanced']
        provenance = receipt.parent / advanced['provenance']
        if not provenance.is_file() or core.sha(provenance) != advanced['provenanceSha256']:
            raise ValueError('Advanced panorama provenance changed')
        # Prepared D stays original even when optional endpoint capping makes
        # raw decoded alpha smaller: nativeRGBA8 decoding applies minDivisor D.
        advanced_kwargs = {'advanced_alpha_policy': alpha_policy} if alpha_policy else {}
        result = _export_texture(receipt.parent / advanced['master'], advanced['masterSha256'],
            ADVANCED_FILENAME, [128,768], work, package, ktx, threads, fixed_divisor=True, **advanced_kwargs)
        descriptor['advanced'] = advanced_payload(advanced, result['textureSha256'], result['metrics']['alpha_min']/255)
        report['advanced'] = result
    metadata[KEY] = descriptor
    return report
