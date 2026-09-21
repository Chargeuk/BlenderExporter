import bpy

if "bpy" in locals():
    import importlib
    if 'support' in locals():
        importlib.reload(support)