"""Opt-in block RGBD and volume routing; pure CPU, no capture or real KTX claim."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import numpy as np

ROOT=Path(__file__).resolve().parents[1]/'src/babylon_js'
PACKAGE='rgbd_block4_test_package'
package=types.ModuleType(PACKAGE);package.__path__=[str(ROOT)];sys.modules[PACKAGE]=package
def load(name):
    spec=importlib.util.spec_from_file_location(PACKAGE+'.'+name,ROOT/(name+'.py'))
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
    return module
core=load('ktx_conversion');volume=load('environment_lighting_volume')


class BlockRGBD(unittest.TestCase):
    def test_default_legacy_bytes_are_fixed_golden_values(self):
        rgb=np.array([[[0,0,0],[1,1,1],[3,3,3],[15,15,15],[51,0,51],[255,255,255]]],np.float32)
        expected=np.array([[[0,0,0,255],[255,255,255,255],[255,255,255,85],
                            [255,255,255,17],[255,0,255,5],[255,255,255,1]]],np.uint8)
        for mode in (None,'per-pixel'):
            actual,_=core.encode_rgbd(rgb) if mode is None else core.encode_rgbd(rgb,mode)
            np.testing.assert_array_equal(actual,expected)

    def test_advanced_fixed_bytes_and_metadata_stay_unchanged(self):
        rgb=np.zeros((4,4,3),np.float32);rgb[1,2]=[51,0,51]
        expected=np.zeros((4,4,4),np.uint8);expected[...,3]=5;expected[1,2]=[255,0,255,5]
        fixed,original=core.encode_rgbd(rgb,fixed_divisor=True)
        np.testing.assert_array_equal(fixed,expected)
        for mode in ('per-pixel','image-wide','block-4x4'):
            actual,metrics=core.encode_rgbd(rgb,mode,fixed_divisor=True)
            np.testing.assert_array_equal(actual,expected);self.assertEqual(metrics,original)
        self.assertNotIn('rgbd_block_size',original)

    def test_each_block_exactly_matches_existing_fixed_encoder(self):
        rgb=np.arange(8*12*3,dtype=np.float32).reshape(8,12,3)/10
        pixels,metrics=core.encode_rgbd(rgb,'block-4x4')
        for y in range(0,8,4):
            for x in range(0,12,4):
                expected,_=core.encode_rgbd(rgb[y:y+4,x:x+4],fixed_divisor=True)
                np.testing.assert_array_equal(pixels[y:y+4,x:x+4],expected)
        self.assertEqual(metrics['rgbd_encoded_divisor_mode'],'block-4x4')
        self.assertEqual(metrics['rgbd_block_size'],[4,4])
        self.assertIsNone(metrics['rgbd_image_divisor'])

    def test_block_values_vary_without_clipping_hdr_or_modifying_source(self):
        rgb=np.ones((8,8,3),np.float32)
        rgb[:4,4:]*=3;rgb[4:,:4]*=15;rgb[4:,4:]*=51
        before=rgb.copy();rgb.setflags(write=False)
        actual,_=core.encode_rgbd(rgb,'block-4x4')
        self.assertEqual(set(np.unique(actual[...,3])),{255,85,17,5})
        self.assertTrue((rgb*(actual[...,3:4]/255)<=1+np.finfo(np.float32).eps).all())
        restored=(actual[...,:3].astype(float)/255)**2.2/(actual[...,3:4]/255)
        np.testing.assert_allclose(restored,rgb,rtol=1e-6,atol=1e-6)
        np.testing.assert_array_equal(rgb,before)

    def test_block_shape_guards_are_specific_to_opt_in(self):
        for shape in ((3,4,3),(4,5,3),(0,4,3),(4,0,3),(4,4,4),(4,3),(1,4,4,3)):
            with self.subTest(shape=shape),self.assertRaisesRegex(ValueError,'dimensions divisible by four'):
                core.encode_rgbd(np.zeros(shape,np.float32),'block-4x4')
        core.encode_rgbd(np.zeros((1,1,3),np.float32))
        core.encode_rgbd(np.zeros((1,1,3),np.float32),'image-wide')

    def test_invalid_energy_and_options_never_silently_clip(self):
        for value in (-1,np.nan,np.inf,256):
            with self.subTest(value=value),self.assertRaisesRegex(ValueError,'not clipped'):
                core.encode_rgbd(np.full((4,4,3),value,np.float32),'block-4x4')
        for mode in ('block4',False,None):
            with self.subTest(mode=mode),self.assertRaisesRegex(ValueError,'Unsupported RGBD divisor mode'):
                core.encode_rgbd(np.ones((4,4,3)),mode)

    def test_standalone_preparation_forwards_block_mode(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'source.exr';source.write_bytes(b'EXR fixture')
            rgb=np.arange(8*8*3,dtype=np.float32).reshape(8,8,3)/10
            spec=dict(source_path=str(source),output='block.ktx2',size=0,encoding='rgbd-v1',
                      rgbd_divisor_mode='block-4x4',color_space='linear',alpha='preserve',flip_y=False)
            with patch.object(core,'read_float_hdr',return_value=rgb[...,::-1]):
                prepared,metrics=core.prepare(spec,root)
            expected,_=core.encode_rgbd(rgb,'block-4x4')
            np.testing.assert_array_equal(np.asarray(Image.open(prepared)),expected)
            self.assertEqual(metrics['rgbd_encoded_divisor_mode'],'block-4x4')
            self.assertEqual(metrics['rgbd_block_size'],[4,4])

    def test_conversion_plan_accepts_only_explicit_rgbd_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'source.exr').write_bytes(b'fixture')
            (root/'scene.babylon').write_text(json.dumps(dict(meshes=[dict(name='lightmap_old',id='stable')])))
            texture=dict(source='source.exr',output='block.ktx2',size=0,flip_y=False,color_space='linear',
                         codec='uastc',mipmaps=False,alpha='preserve',encoding='rgbd-v1',rgbd_divisor_mode='block-4x4',
                         lightmap_markers=['lightmap_old'])
            path=root/'plan.json';config=dict(version=1,output_dir='out',models=[dict(source='scene.babylon')],textures=[texture])
            path.write_text(json.dumps(config));self.assertEqual(core.load_plan(path)['textures'][0]['rgbd_divisor_mode'],'block-4x4')
            texture['encoding']='legacy';path.write_text(json.dumps(config))
            with self.assertRaisesRegex(ValueError,'requires rgbd-v1'):core.load_plan(path)


class VolumeOptIn(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.receipt=self.root/'receipt.json';self.receipt.write_text('{}')
        self.master=self.root/'simple.exr';self.master.write_bytes(b'unchanged Simple master')
        self.provenance=self.root/'advanced.json';self.provenance.write_text('{}')
        self.record=dict(version=1,dimensions=[32,16,32],atlasColumns=4,atlasRows=4,atlasLayout=volume.LAYOUT,
            bounds=dict(min=[-4.5,-.05,-4],max=[4.5,2.8,4]),master='simple.exr',masterSha256=core.sha(self.master),flip_y=False,
            normalization=dict(method='solid-angle-rgb-mean-over-env-reference',referenceRgb=[1,1,1]))
        self.context=types.SimpleNamespace(scene=types.SimpleNamespace(world=types.SimpleNamespace(preserveZUpRight=False)))
        self.bpy=types.SimpleNamespace(path=types.SimpleNamespace(abspath=lambda x:x))

    def export(self,options,advanced=False):
        record=copy.deepcopy(self.record)
        if advanced:
            record['advanced']=dict(master='advanced.exr',masterSha256='b'*64,provenance='advanced.json',provenanceSha256=core.sha(self.provenance))
        def result(*args,**kwargs):
            return dict(status='passed',texture=args[2],textureSha256='c'*64,metrics=dict(alpha_min=5 if kwargs.get('fixed_divisor') else 14))
        model=dict(meshes=[dict(name='hasenv')]);before=self.master.read_bytes()
        with patch.dict(sys.modules,{'bpy':self.bpy}),patch.object(volume,'validate_record',return_value=record),patch.object(volume,'validate_source'),patch.object(volume,'advanced_payload',return_value=dict(test='Advanced preserved')),patch.object(volume,'_export_texture',side_effect=result) as encode:
            report=volume.export_volume(self.context,model,self.root,self.root,dict(config=str(self.receipt),**options),'ktx',8)
        self.assertEqual(self.master.read_bytes(),before)
        return report,model,encode.call_args_list

    def test_legacy_default_routing_does_not_opt_in(self):
        report,model,calls=self.export({})
        self.assertEqual(calls[0].kwargs,{})
        self.assertEqual(model['meshes'][0]['metadata'][volume.KEY]['minDivisor'],14/255)
        self.assertNotIn('advanced',report)

    def test_opt_in_only_changes_simple_route_and_retains_advanced_fixed(self):
        report,model,calls=self.export(dict(simple_divisor_mode='block-4x4'),advanced=True)
        self.assertEqual(calls[0].kwargs,dict(divisor_mode='block-4x4'))
        self.assertEqual(calls[1].kwargs,dict(fixed_divisor=True))
        self.assertEqual(report['advanced']['texture'],volume.ADVANCED_FILENAME)
        self.assertEqual(model['meshes'][0]['metadata'][volume.KEY]['strength'],1)

    def test_invalid_simple_policy_rejects_before_any_export_or_metadata(self):
        for mode in ('image-wide','block4',False,None):
            model=dict(meshes=[dict(name='hasenv')]);before=copy.deepcopy(model)
            with self.subTest(mode=mode),patch.dict(sys.modules,{'bpy':self.bpy}),patch.object(volume,'_export_texture') as encode:
                with self.assertRaisesRegex(ValueError,'Unsupported Simple volume divisor mode'):
                    volume.export_volume(self.context,model,self.root,self.root,dict(simple_divisor_mode=mode),'ktx')
                encode.assert_not_called();self.assertEqual(model,before)

    def test_blender_prepare_route_receives_explicit_mode_without_codec_change(self):
        import OpenImageIO as oiio
        from PIL import Image
        # Exercise the real pre-encoder byte guard with valid images. Only Blender preparation
        # and the external KTX process remain routing stand-ins; no real KTX encoding is claimed.
        master=np.empty((128,128,3),np.float32);master[:]=[2,.5,1]
        image=oiio.ImageBuf(oiio.ImageSpec(128,128,3,oiio.FLOAT))
        image.set_pixels(oiio.ROI.All,master)
        self.assertTrue(image.write(str(self.master)),image.geterror())
        rgba,_=core.encode_rgbd(master,'block-4x4')
        self.assertEqual(int(rgba[...,3].min()),127)
        self.assertGreater(int(rgba[0,0,0]),int(rgba[0,0,3]))
        prepared=self.root/'prepared.png';Image.fromarray(rgba,'RGBA').save(prepared);captured=[]
        def prepare(spec,stage):
            captured.append(spec);return prepared,dict(dimensions=[128,128],alpha_min=int(rgba[...,3].min()))
        fake=types.ModuleType(PACKAGE+'.ktx_export');fake.prepare_blender=prepare;fake.process_options=lambda:{}
        def run(argv,**kwargs):
            if argv[1]=='create':Path(argv[-1]).write_bytes(b'KTX routing fixture, not real encoding')
            return types.SimpleNamespace(returncode=0,stdout='',stderr='')
        with patch.dict(sys.modules,{PACKAGE+'.ktx_export':fake}),patch('subprocess.run',side_effect=run):
            result=volume._export_texture(self.master,core.sha(self.master),volume.FILENAME,[128,128],self.root,self.root,'ktx',8,divisor_mode='block-4x4')
        self.assertEqual(captured[0]['rgbd_divisor_mode'],'block-4x4')
        self.assertFalse(captured[0]['rgbd_fixed_divisor']);self.assertFalse(captured[0]['flip_y']);self.assertFalse(captured[0]['mipmaps'])
        self.assertEqual(captured[0]['codec'],'uastc');self.assertEqual(result['metrics']['rgbd_divisor_mode'],'block-4x4')
        self.assertEqual(result['command'][result['command'].index('--uastc-quality')+1],'4')
        self.assertTrue(result['metrics']['source_preparation_guard']['byte_exact'])
        self.assertEqual(result['metrics']['source_preparation_guard']['when'],'before-ktx-create')


if __name__=='__main__':unittest.main()
