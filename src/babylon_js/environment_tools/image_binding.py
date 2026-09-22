"""Explicitly replace image buffers from disk, including packed source images."""
from pathlib import Path
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
