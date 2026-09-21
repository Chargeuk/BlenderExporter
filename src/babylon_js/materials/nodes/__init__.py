if "bpy" in locals():
    import importlib
    if 'abstract' in locals():
        importlib.reload(abstract)
    if 'ambient_occlusion' in locals():
        importlib.reload(ambient_occlusion)
    if 'background' in locals():
        importlib.reload(background)
    if 'diffuse' in locals():
        importlib.reload(diffuse)
    if 'emission' in locals():
        importlib.reload(emission)
    if 'fresnel' in locals():
        importlib.reload(fresnel)
    if 'glossy' in locals():
        importlib.reload(glossy)
    if 'gltf' in locals():
        importlib.reload(gltf)
    if 'normal_map' in locals():
        importlib.reload(normal_map)
    if 'mapping' in locals():
        importlib.reload(mapping)
    if 'passthru' in locals():
        importlib.reload(passthru)
    if 'principled' in locals():
        importlib.reload(principled)
    if 'refraction' in locals():
        importlib.reload(refraction)
    if 'tex_coord' in locals():
        importlib.reload(tex_coord)
    if 'tex_environment' in locals():
        importlib.reload(tex_environment)
    if 'tex_image' in locals():
        importlib.reload(tex_image)
    if 'transparency' in locals():
        importlib.reload(transparency)
    if 'unsupported' in locals():
        importlib.reload(unsupported)
    if 'uv_map' in locals():
        importlib.reload(uv_map)

import bpy