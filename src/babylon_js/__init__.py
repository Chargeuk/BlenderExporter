bl_info = {
    'name': 'Babylon.js',
    'author': 'David Catuhe, Jeff Palmer',
    'version': (3, 3, 4),
    'blender': (3, 3, 0),
    'location': 'File > Export > Babylon.js (.babylon)',
    'description': 'Export Babylon.js scenes (.babylon)',
    'wiki_url': 'https://github.com/BabylonJS/BlenderExporter',
    'tracker_url': '',
    'category': 'Babylon.JS'}

import bpy
from bpy_extras.io_utils import ExportHelper, ImportHelper

# allow module to be changed during a session (dev purposes)
if "bpy" in locals():
    print('Reloading .babylon exporter')
    import importlib
    if 'materials' in locals():
        importlib.reload(materials)  # directory
    if 'animation' in locals():
        importlib.reload(animation)
    if 'armature' in locals():
        importlib.reload(armature)
    if 'camera' in locals():
        importlib.reload(camera)
    if 'f_curve_animatable' in locals():
        importlib.reload(f_curve_animatable)
    if 'js_exporter' in locals():
        importlib.reload(js_exporter)
    if 'light_shadow' in locals():
        importlib.reload(light_shadow)
    if 'logging' in locals():
        importlib.reload(logging)
    if 'mesh' in locals():
        importlib.reload(mesh)
    if 'package_level' in locals():
        importlib.reload(package_level)
    if 'shape_key_group' in locals():
        importlib.reload(shape_key_group)
    if 'sound' in locals():
        importlib.reload(sound)
    if 'world' in locals():
        importlib.reload(world)

#===============================================================================
def update_ktx_defaults(operator, context):
    from .ktx_export import autofill_lightmap
    autofill_lightmap(operator, context)


class JsonMain(bpy.types.Operator, ExportHelper):
    bl_idname = 'export.bjs'
    bl_label = 'Export Babylon.js scene' # used on the label of the actual 'save' button
    bl_options = {'REGISTER', 'UNDO'}
    filename_ext = '.babylon'            # used as the extension on file selector

    filepath: bpy.props.StringProperty(subtype = 'FILE_PATH') # assigned once the file selector returns
    filter_glob: bpy.props.StringProperty(name='.babylon',default='*.babylon', options={'HIDDEN'})
    export_selected: bpy.props.BoolProperty(
        name='Export only selected objects',
        description='Export only the currently selected objects',
        default=False
    )

    convert_to_ktx2: bpy.props.BoolProperty(
        name='Convert textures to KTX2', default=False, update=update_ktx_defaults,
        description='Stage, prepare PNGs, compress textures and update the exported model; requires KTX-Software 4.4.2+')
    ktx_executable: bpy.props.StringProperty(
        name='KTX executable', subtype='FILE_PATH', default='',
        description='Optional path to ktx; leave empty to find it on PATH')
    ktx_flip_y: bpy.props.BoolProperty(
        name='Flip images vertically', default=True,
        description='Flip prepared PNG copies for Babylon compressed texture orientation; originals remain unchanged')
    ktx_codec: bpy.props.EnumProperty(
        name='Material compression', default='basis-lz',
        items=[('basis-lz', 'ETC1S (smaller files)', 'BasisLZ level 5, quality 255'),
               ('uastc', 'UASTC (higher quality)', 'UASTC quality 4 with Zstandard')])
    ktx_threads: bpy.props.IntProperty(name='Encoder threads', default=8, min=1, max=128)
    ktx_auto_lightmap: bpy.props.BoolProperty(
        name='Find KaDshow lightmap automatically', default=True, update=update_ktx_defaults,
        description='Use a unique saved image from a marker property/name, explicit UV2 connection or lightmap/SimpleBake keyword')
    ktx_lightmap: bpy.props.StringProperty(
        name='KaDshow lightmap image', subtype='FILE_PATH', default='',
        description='Optional source PNG/EXR for a lightmap_ parent; UASTC, sRGB PNG baseline, no mipmaps')
    ktx_lightmap_marker: bpy.props.StringProperty(
        name='Lightmap marker', default='',
        description='Exact exported lightmap_ node name; empty selects the only marker in the export')

    def execute(self, context):
        from .json_exporter import JsonExporter
        from .package_level import get_title, verify_min_blender_version

        if not verify_min_blender_version():
            self.report({'ERROR'}, 'version of Blender too old.')
            return {'CANCELLED'}

        exporter = JsonExporter()
        objects = bpy.context.selected_objects if self.export_selected else bpy.context.scene.objects
        options = None
        if self.convert_to_ktx2:
            options = dict(executable=self.ktx_executable, flip_y=self.ktx_flip_y,
                           codec=self.ktx_codec, threads=self.ktx_threads,
                           lightmap=self.ktx_lightmap, lightmap_marker=self.ktx_lightmap_marker,
                           auto_lightmap=self.ktx_auto_lightmap)
        exporter.execute(context, self.filepath, objects, ktx_options=options)

        if (exporter.fatalError):
            self.report({'ERROR'}, exporter.fatalError)
            return {'CANCELLED'}

        elif (exporter.nErrors > 0):
            self.report({'ERROR'}, 'Output cancelled due to data error, See log file.')
            return {'CANCELLED'}

        elif (exporter.nWarnings > 0):
            self.report({'WARNING'}, 'Processing completed, but ' + str(exporter.nWarnings) + ' WARNINGS were raised,  see log file.')

        else:
            self.report({'INFO'}, 'Export Completed')

        return {'FINISHED'}

    def draw(self, context):
        self.layout.label(
            text='Other export settings in properties panels'
        )
        self.layout.prop(self, 'export_selected')
        from .ktx_export import find_ktx
        box = self.layout.box()
        box.prop(self, 'ktx_executable')
        tool, status = find_ktx(self.ktx_executable)
        row = box.row()
        row.enabled = bool(tool) or self.convert_to_ktx2
        row.prop(self, 'convert_to_ktx2')
        box.label(text=status, icon='CHECKMARK' if tool else 'INFO')
        if self.convert_to_ktx2:
            options = box.column()
            options.enabled = bool(tool)
            options.prop(self, 'ktx_flip_y')
            options.prop(self, 'ktx_codec')
            options.prop(self, 'ktx_threads')
            options.prop(self, 'ktx_auto_lightmap')
            options.prop(self, 'ktx_lightmap')
            options.prop(self, 'ktx_lightmap_marker')
            if self.ktx_auto_lightmap and not self.ktx_lightmap:
                from .ktx_export import infer_lightmap
                objects = context.selected_objects if self.export_selected else context.scene.objects
                found = infer_lightmap(context, objects, self.ktx_lightmap_marker)
                box.label(text=found['reason'], icon='INFO')
            if context.scene.world and context.scene.world.inlineTextures:
                box.label(text='Disable Inline textures in World settings', icon='ERROR')
#===============================================================================
# The list of classes which sub-class a Blender class, which needs to be registered
from . import camera
from . import light_shadow
from . import materials # directory
from . import world # must be defined before mesh
from . import mesh
classes = (
    # Operator sub-classes
    JsonMain,

    # Panel sub-classes
    camera.BJS_PT_CameraPanel,
    light_shadow.BJS_PT_LightPanel,
    materials.material.BJS_PT_MaterialsPanel,
    mesh.BJS_PT_MeshPanel,
    world.BJS_PT_WorldPanel
)

def register():
    from bpy.utils import register_class
    for cls in classes:
        register_class(cls)
    bpy.types.TOPBAR_MT_file_export.append(menu_func)

def unregister():
    from bpy.utils import unregister_class
    for cls in reversed(classes):
        unregister_class(cls)

    bpy.types.TOPBAR_MT_file_export.remove(menu_func)

# Registration the calling of the INFO_MT_file_export file selector
def menu_func(self, context):
    from .package_level import get_title
    # the info for get_title is in this file, but getting it the same way as others
    self.layout.operator(JsonMain.bl_idname, text=get_title())

if __name__ == '__main__':
    unregister()
    register()
