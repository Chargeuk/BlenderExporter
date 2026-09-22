import bpy,json,sys
import numpy as np
from pathlib import Path
args=sys.argv[sys.argv.index('--')+1:];source=Path(args[0]);out=Path(args[1])
assert source.is_file() and not out.exists()
s=bpy.context.scene
im=bpy.data.images.load(str(source),check_existing=False);im.colorspace_settings.name='Linear Rec.709'
w,h=im.size
pixels=np.empty(w*h*4,dtype=np.float32);im.pixels.foreach_get(pixels);pixels=pixels.reshape(h,w,4)
border=64
context=np.concatenate([pixels[:,-border:],pixels,pixels[:,:border]],axis=1)
padded=bpy.data.images.new('HDR horizontal wrap context',width=w+2*border,height=h,float_buffer=True)
padded.colorspace_settings.name='Linear Rec.709';padded.pixels.foreach_set(context.ravel())
g=bpy.data.node_groups.new('HDR panorama denoising','CompositorNodeTree')
g.interface.new_socket(name='Image',in_out='OUTPUT',socket_type='NodeSocketColor')
n=g.nodes;l=g.links
image=n.new('CompositorNodeImage');image.image=padded
denoise=n.new('CompositorNodeDenoise');denoise.inputs['HDR'].default_value=True
output=n.new('NodeGroupOutput');l.new(image.outputs['Image'],denoise.inputs['Image']);l.new(denoise.outputs['Image'],output.inputs['Image'])
s.compositing_node_group=g
s.render.engine='CYCLES';s.cycles.samples=1;s.cycles.use_denoising=False
s.render.resolution_x=w+2*border;s.render.resolution_y=h;s.render.resolution_percentage=100
s.render.image_settings.file_format='OPEN_EXR';s.render.image_settings.color_mode='RGB';s.render.image_settings.color_depth='32'
s.render.filepath=str(out.with_name(out.stem+'_context.exr'))
bpy.ops.render.render(write_still=True)
result=bpy.data.images.load(s.render.filepath,check_existing=False)
result.colorspace_settings.name='Linear Rec.709'
a=np.empty((w+2*border)*h*4,dtype=np.float32);result.pixels.foreach_get(a)
a=a.reshape(h,w+2*border,4)[:,border:border+w].copy()
final=bpy.data.images.new('KaDshow_EnvironmentHDR',width=w,height=h,float_buffer=True)
final.colorspace_settings.name='Linear Rec.709';final.pixels.foreach_set(a.ravel())
final.filepath_raw=str(out);final.file_format='OPEN_EXR';final.save_render(str(out),scene=s)
print('DENOISED_HDR_SAVED',str(out),flush=True)
