"""Convert an explicitly rendered linear room panorama into Babylon's .env."""
from functools import lru_cache
import json
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess

from . import ktx_conversion as core
from .ktx_export import process_options


def helper_path(override=''):
    import bpy
    if override:
        return Path(bpy.path.abspath(override)).resolve()
    addon = bpy.context.preferences.addons.get('babylon_js')
    configured = getattr(addon.preferences, 'env_converter_script', '') if addon else ''
    if configured:
        return Path(bpy.path.abspath(configured)).resolve()
    return Path(__file__).resolve().parents[2] / 'tools' / 'env-converter' / 'convert.cjs'


@lru_cache(maxsize=8)
def _probe(node, script, modified):
    try:
        r = subprocess.run([node, script, '--check'],capture_output=True,text=True,timeout=15,**process_options())
        data = json.loads(r.stdout) if r.returncode == 0 else {}
        if data.get('status') != 'ready' or data.get('babylon') != '7.27.0':
            return '', 'Configure the local ENV converter (Babylon 7.27 and Chromium required)'
        return node, 'Babylon 7.27 ENV converter ready'
    except (OSError, ValueError, subprocess.SubprocessError):
        return '', 'Cannot run ENV converter; check Node, npm dependencies and Chromium'


def find_converter(override=''):
    script = helper_path(override)
    node = shutil.which('node')
    if not node or not script.is_file():
        return '', str(script), 'Configure the ENV converter script and install Node.js'
    executable, message = _probe(node,str(script),script.stat().st_mtime_ns)
    return executable, str(script), message


def infer_capture(context):
    """A room capture must be explicitly associated; never infer from a World HDRI."""
    import bpy
    world = context.scene.world
    value = world.get('bjs_environment_image','') if world else ''
    if value:
        image = bpy.data.images.get(str(value))
        return bpy.path.abspath(image.filepath) if image else bpy.path.abspath(str(value))
    image = bpy.data.images.get('KaDshow_EnvironmentHDR')
    return bpy.path.abspath(image.filepath) if image and image.filepath else ''


def autofill_environment(operator, context):
    if operator.export_environment and not operator.environment_image:
        operator.environment_image = infer_capture(context)


def write_hdr(path, rgb):
    """Radiance RGBE, modern scanline RLE with literal packets; values stay linear."""
    import numpy as np
    h,w = rgb.shape[:2]
    if not 8 <= w <= 32767:
        raise ValueError('HDR width must be between 8 and 32767')
    maximum = np.max(rgb, axis=2)
    mantissa, exponent = np.frexp(maximum)
    scale = np.divide(mantissa*256, maximum, out=np.zeros_like(maximum), where=maximum>1e-32)
    rgbe = np.empty((h,w,4),dtype=np.uint8)
    rgbe[:,:,:3] = np.minimum(rgb*scale[:,:,None],255).astype(np.uint8)
    rgbe[:,:,3] = np.where(maximum>1e-32,exponent+128,0).astype(np.uint8)
    with path.open('wb') as f:
        f.write(f'#?RADIANCE\nFORMAT=32-bit_rle_rgbe\n\n-Y {h} +X {w}\n'.encode('ascii'))
        for row in rgbe:
            f.write(bytes((2,2,w>>8,w&255)))
            for channel in range(4):
                for x in range(0,w,128):
                    values=row[x:x+128,channel].tobytes()
                    f.write(bytes((len(values),)));f.write(values)


def prepare_capture(source, destination, exposure=0, gain=1, threshold=1):
    import numpy as np
    from .skybox_export import read_panorama
    if source.suffix.lower() not in ('.exr','.hdr'):
        raise ValueError('ENV capture must be a linear EXR/HDR, not an LDR skybox image')
    if not all(math.isfinite(v) for v in (exposure,gain,threshold)) or not -20<=exposure<=20 or not 1<=gain<=16 or threshold<=0:
        raise ValueError('Invalid ENV exposure/highlight settings')
    original_hash=core.sha(source)
    rgb,hdr=read_panorama(source)
    assert hdr
    rgb=np.maximum(rgb,0)*(2.0**exposure)
    before_max=float(rgb.max())
    luminance=rgb @ np.array([.2126,.7152,.0722],dtype=np.float32)
    # Smooth ramp from no boost at threshold to full gain at 2*threshold.
    t=np.clip((luminance-threshold)/threshold,0,1)
    weight=t*t*(3-2*t)
    rgb*= (1+(gain-1)*weight)[...,None]
    if not np.isfinite(rgb).all() or float(rgb.max())>1e30:
        raise ValueError('HDR radiance is outside the supported RGBE range')
    write_hdr(destination,rgb)
    if core.sha(source)!=original_hash:
        raise RuntimeError('Environment source changed during preparation')
    return dict(source=str(source),source_sha256=original_hash,dimensions=[rgb.shape[1],rgb.shape[0]],
                exposure_stops=exposure,highlight_gain=gain,highlight_threshold=threshold,
                highlight_pixels_percent=float(np.mean(weight>0)*100),linear_max_before_boost=before_max,
                linear_max_after_boost=float(rgb.max()),prepared_hdr=str(destination),prepared_sha256=core.sha(destination))


def validate_env(path, size):
    data=path.read_bytes()
    if data[:8]!=bytes([0x86,0x16,0x87,0x96,0xf6,0xd6,0x96,0x36]):
        raise ValueError('Invalid ENV magic')
    end=data.index(b'\0',8);header=json.loads(data[8:end])
    entries=header.get('specular',{}).get('mipmaps',[])
    if header.get('version') not in (1,2) or header.get('width')!=size or len(entries)!=6*(int(math.log2(size))+1):
        raise ValueError('Invalid ENV dimensions or mip levels')
    coefficients=header.get('irradiance',{})
    if set(coefficients)!={'x','y','z','xx','yy','zz','yz','zx','xy'} or not all(len(v)==3 and all(math.isfinite(x) for x in v) for v in coefficients.values()):
        raise ValueError('Invalid ENV irradiance')
    for index,entry in enumerate(entries):
        start=end+1+entry['position'];length=entry['length']
        png=data[start:start+length]
        expected=max(1,size>>(index//6))
        if len(png)!=length or png[:8]!=b'\x89PNG\r\n\x1a\n' or struct.unpack('>II',png[16:24])!=(expected,expected):
            raise ValueError('Invalid ENV face payload')
    return dict(version=header['version'],face_size=size,mip_levels=int(math.log2(size))+1,
                faces=6,bytes=len(data),sha256=core.sha(path))


def add_marker(model):
    meshes=model.setdefault('meshes',[])
    matches=[m for m in meshes if m.get('name','').lower()=='hasenv']
    if len(matches)>1 or any(m.get('indices') or m.get('positions') for m in matches):
        raise ValueError('ENV marker must be a single empty node')
    if matches:return 'retained existing hasenv marker'
    ids={m.get('id') for key in ('meshes','transformNodes','lights','cameras') for m in model.get(key,[])}
    node_id='hasenv';i=1
    while node_id in ids:node_id='hasenv_'+str(i);i+=1
    meshes.append(dict(name='hasenv',id=node_id,position=[0,0,0],rotation=[0,0,0],scaling=[1,1,1],isVisible=False,isEnabled=True))
    return 'added delivery-only hasenv marker'


def export_environment(context, model, work, package, options):
    import bpy
    node,script,message=find_converter(options.get('converter',''))
    if not node:raise ValueError(message)
    path=options.get('image') or infer_capture(context)
    if not path:raise ValueError('Select a rendered room HDR capture; the World sky image is not an ENV capture')
    source=Path(bpy.path.abspath(path)).resolve()
    if not source.is_file():raise ValueError('Environment capture does not exist: '+str(source))
    size=int(options.get('size',512))
    if size not in (128,256,512,1024):raise ValueError('ENV size must be 128, 256, 512 or 1024')
    directory=work/'environment';directory.mkdir()
    report=prepare_capture(source,directory/'prepared.hdr',float(options.get('exposure',0)),
                           float(options.get('highlight_gain',1)),float(options.get('highlight_threshold',1)))
    config=dict(input=str(directory/'prepared.hdr'),output=str(directory/'environment.env'),size=size,
                filterSamples=256,preview=str(directory/'ibl-probes.png'),report=str(directory/'converter-report.json'))
    config_path=directory/'converter-config.json';config_path.write_text(json.dumps(config,indent=2),encoding='utf8')
    command=[node,script,str(config_path)]
    result=subprocess.run(command,capture_output=True,text=True,timeout=1800,**process_options())
    (directory/'converter.log').write_text(result.stdout+result.stderr,encoding='utf8')
    if result.returncode or not Path(config['output']).is_file():
        raise RuntimeError('ENV conversion failed; see '+str(directory/'converter.log'))
    verified=validate_env(Path(config['output']),size)
    converter=core.read_json(Path(config['report']))
    if converter.get('status')!='passed' or not converter.get('reloaded'):
        raise ValueError('Converted ENV did not pass Babylon reload validation')
    if core.sha(source)!=report['source_sha256']:raise RuntimeError('Capture changed during conversion')
    if (package/'environment.env').exists():raise ValueError('Environment output filename collision')
    shutil.copy2(config['output'],package/'environment.env')
    marker=add_marker(model)
    return dict(status='passed',output='environment.env',marker_change=marker,command=command,
                converter=converter,**report,**verified)
