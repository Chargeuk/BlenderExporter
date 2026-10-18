"""Explicitly replace image buffers from disk, including packed source images."""
from pathlib import Path
from contextlib import contextmanager
import bpy


def replace_from_disk(image,target):
    path=Path(target).resolve()
    if not path.is_file():raise FileNotFoundError(path)
    if image.source!='FILE':raise ValueError('Only single FILE images can be rebound')
    fresh=bpy.data.images.load(str(path),check_existing=False)
    fresh.colorspace_settings.name=image.colorspace_settings.name
    fresh.alpha_mode=image.alpha_mode
    fresh.use_fake_user=image.use_fake_user
    for key in image.keys():fresh[key]=image[key]
    old_name=image.name
    # Remaps every user, including image nodes nested in groups and image editors.
    image.user_remap(fresh)
    bpy.data.images.remove(image)
    fresh.name=old_name
    return fresh


@contextmanager
def missing_generated_preflight(config, enabled=False):
    """Dimensions only in a disposable fresh preflight; never save bake inputs."""
    swapped=[];records=[]
    try:
        if enabled:
            if not bpy.app.background:raise ValueError('Missing-map preparation requires disposable background Blender')
            roles=['direct','indirect','island_ids']
            if config.get('ao',{}).get('enabled'):roles.append('ao')
            paths={(Path(config['_root'])/config['images'][role]).resolve():role for role in roles}
            authored={(Path(config['_root'])/path).resolve() for path in config.get('source_images',[])}
            authored.update((Path(config['_root'])/row['path']).resolve() for row in config.get('downloads',[]))
            if set(paths)&authored:raise ValueError('Generated preflight targets overlap authored inputs')
            for image in list(bpy.data.images):
                if image.source!='FILE' or not image.filepath or image.packed_file:continue
                path=Path(bpy.path.abspath(image.filepath)).resolve()
                if path not in paths or path.is_file():continue
                temporary=bpy.data.images.new('TEMP non-promotable preflight '+paths[path],config['size'],config['size'],alpha=True,float_buffer=True)
                temporary.colorspace_settings.name=image.colorspace_settings.name
                temporary.alpha_mode=image.alpha_mode
                temporary['bjs_preflight_placeholder']=True
                image.user_remap(temporary);swapped.append((image,temporary))
                records.append(dict(role=paths[path],missingPath=str(path),dimensions=[config['size'],config['size']],
                    nonPromotable=True,purpose='Dimension validation only; no radiance, ownership or bake reuse'))
        yield records
    finally:
        for original,temporary in reversed(swapped):
            temporary.user_remap(original)
            bpy.data.images.remove(temporary)
