import bpy

if "bpy" in locals():
    import importlib
    if 'nodes' in locals():
        importlib.reload(nodes)  # directory
    if 'env_textures' in locals():
        importlib.reload(env_textures)  # directory
    if 'baking_recipe' in locals():
        importlib.reload(baking_recipe)
    if 'material' in locals():
        importlib.reload(material)
    if 'texture' in locals():
        importlib.reload(texture)