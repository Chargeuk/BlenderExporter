"""Focused offline volume checks; synthetic EXR/codec seams, no Blender/render.

The full artifact fixture exercises hashes/schema/model/PNG/DFD/commands/report.
Its EXR reader and official executable are explicit doubles: this suite does
not claim genuine EXR decoding, UASTC quality, or native KTX integration.
"""
import copy
import importlib.util
import json
import math
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import numpy as np
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]/'src/babylon_js'
spec=importlib.util.spec_from_file_location('decoded_volume_test',ROOT/'workflow_tools/decoded_delivery.py')
delivery=importlib.util.module_from_spec(spec);spec.loader.exec_module(delivery)
volume,_=delivery._volume_contract_helpers()


def thresholds(limit=1):
    return {key:dict(mean=limit,p95=limit,maximum=limit)
            for key in ('linear_vs_master','codec_vs_prepared','preparation_vs_master')}


def masters(advanced):
    y,x=np.indices((768 if advanced else 128,128))
    # Distinct directional banks and gradients expose bank swaps and flips.
    rgb=np.empty((len(y),128,3),np.float32)
    rgb[...,0]=1+x/40+(y//128 if advanced else y/80)
    rgb[...,1]=.5+(y%128)/50
    rgb[...,2]=.25+x/150
    return rgb


def native_receipt():
    bounds=dict(min=[-6,.15,-6],max=[6,3.4,6])
    return dict(version=1,dimensions=list(volume.DIMENSIONS),atlasColumns=4,atlasRows=4,atlasLayout=volume.LAYOUT,
        bounds=bounds,master='simple.exr',masterSha256='a'*64,flip_y=False,
        normalization=dict(method='solid-angle-rgb-mean-over-env-reference',referenceRgb=[1,2,3]),
        advanced=dict(version=1,dimensions=list(volume.DIMENSIONS),bounds=copy.deepcopy(bounds),
            atlasColumns=4,atlasRows=24,atlasLayout=volume.ADVANCED_LAYOUT,directionOrder=list(volume.DIRECTION_ORDER),
            panoramaConvention=volume.PANORAMA_CONVENTION,flip_y=False,master='advanced.exr',masterSha256='b'*64,
            provenance='advanced_provenance.json',provenanceSha256='c'*64,
            normalization=dict(method='cosine-hemisphere-rgb-mean-over-env-reference',referenceRgb=[[1,2,3]]*6)))


def container(width,height):
    # Minimal profile fixture, intentionally not a real compressed texture.
    dfd=bytearray(44);struct.pack_into('<IIHH',dfd,0,44,0,2,40)
    struct.pack_into('<4B',dfd,12,166,1,1,0);dfd[31]=3
    header=b'\xabKTX 20\xbb\r\n\x1a\n'+struct.pack('<13I2Q',0,1,width,height,0,0,1,1,2,104,44,0,0,0,0)
    return header+struct.pack('<3Q',148,4,16)+dfd+b'data'


class ArtifactFixture:
    def __init__(self,base):
        self.base=Path(base);self.calls=[];self.decoded={};self.arrays={}
        def blob(name,data=b'fixture'):
            path=self.base/name;path.write_bytes(data);return path
        def write(name,value):
            path=self.base/name;delivery.save(path,value);return path
        self.write=write
        self.ktx=blob('ktx.exe')
        source={}
        for key in ('scene','scopeRecipe','combined','combinedProvenance','reference'):
            path=blob(key+'.fixture',key.encode());source[key]=str(path);source[key+'Sha256']=delivery.sha(path)
        world=blob('world.exr');source['worldImageSha256']=delivery.sha(world)
        config=write('capture.json',{'scene':source['scene']})
        signature=write('capture_inputs.json',dict(source=source,configSha256=delivery.sha(config)))
        self.receipt=native_receipt();self.receipt['source']=source
        provenance=write('advanced_provenance.json',dict(source=source,acceptedCaptureInputsSha256=delivery.sha(signature),
            directionOrder=list(volume.DIRECTION_ORDER),panoramaConvention=volume.PANORAMA_CONVENTION,
            cameraEulerBlender=[math.pi/2,0,0]))
        self.receipt['advanced']['provenanceSha256']=delivery.sha(provenance)
        self.rows={};self.items={};self.divisors={}
        for name,advanced in (('simple',False),('advanced',True)):
            master=masters(advanced);encoded,metrics=delivery.core.encode_rgbd(master,fixed_divisor=advanced)
            master_path=blob(name+'.exr',master.tobytes());self.arrays[str(master_path)]=master
            prepared=self.base/(name+'.png');Image.fromarray(encoded,'RGBA').save(prepared)
            filename=volume.ADVANCED_FILENAME if advanced else volume.FILENAME
            compressed=blob(filename,container(128,768 if advanced else 128))
            self.decoded[str(compressed)]=encoded.copy();self.divisors[name]=int(encoded[...,3].min())/255
            capture=self.receipt['advanced'] if advanced else self.receipt;capture['masterSha256']=delivery.sha(master_path)
            metrics.update(source_sha256=delivery.sha(master_path),prepared_sha256=delivery.sha(prepared),
                source_dimensions=[128,len(master)],dimensions=[128,len(master)],channels=4,prepared=str(prepared))
            if advanced:metrics['rgbd_divisor_mode']='global-fixed'
            self.rows[name]=dict(status='passed',texture=filename,textureSha256=delivery.sha(compressed),source=str(master_path),
                metrics=metrics,command=[str(self.ktx),'create','--format','R8G8B8A8_UNORM','--assign-oetf','linear',
                    '--encode','uastc','--threads','2','--uastc-quality','4','--zstd','3',str(prepared),str(compressed)])
            self.items[name]=dict(ktx=self.ref(compressed),master=self.ref(master_path),prepared=self.ref(prepared),image_origin='top-left')
        receipt_path=write('volume_receipt.json',self.receipt)
        payload=volume.payload(self.receipt,self.rows['simple']['textureSha256'],self.divisors['simple'],1)
        payload['advanced']=volume.advanced_payload(self.receipt['advanced'],self.rows['advanced']['textureSha256'],self.divisors['advanced'])
        self.model=dict(meshes=[dict(name='hasenv',metadata={volume.KEY:payload})])
        model_path=write('room.babylon',self.model)
        self.report=dict(status='passed',delivery_files=[dict(path=str(model_path),sha256=delivery.sha(model_path))],
            environment_lighting_volume=dict(self.rows['simple'],receipt=str(receipt_path),advanced=self.rows['advanced']))
        report_path=write('conversion.json',self.report)
        self.recipe=dict(schema_version=1,kind='decoded-volume-delivery',model=self.ref(model_path),
            conversion_report=self.ref(report_path),receipt=self.ref(receipt_path),capture_config=self.ref(config),
            capture_inputs=self.ref(signature),source_blend=self.ref(Path(source['scene'])),world_image=self.ref(world),
            volumes=self.items,thresholds={name:thresholds() for name in ('simple','advanced')})
        self.recipe_path=write('validation.json',self.recipe)

    @staticmethod
    def ref(path):return dict(path=str(path),sha256=delivery.sha(path))

    def reseal(self,key,name,value):
        path=self.write(name,value);self.recipe[key]=self.ref(path);self.write('validation.json',self.recipe)

    def run(self,command,**options):
        self.calls.append(command)
        if command[1]=='--version':return subprocess.CompletedProcess(command,0,'ktx version 4.4.2','')
        if command[1]=='extract':Path(command[-1]).write_bytes(self.decoded[command[-2]].tobytes())
        return subprocess.CompletedProcess(command,0,'official fixture double','')

    def validate(self,output='validated'):
        with patch.object(delivery,'volume_linear',side_effect=lambda path:self.arrays[str(path)]),patch.object(delivery.subprocess,'run',side_effect=self.run):
            return delivery.validate_volumes(self.recipe_path,self.base/output,self.ktx)


class VolumeDecodeRegression(unittest.TestCase):
    def test_simple_per_texel_floor_and_pow22_decode(self):
        master=masters(False);prepared,_=delivery.core.encode_rgbd(master)
        actual=prepared.copy();actual[0,0,3]=0
        result=delivery.volume_rgbd_comparison(actual,prepared,master,int(prepared[...,3].min())/255,False,thresholds(10))
        self.assertEqual(result['below_floor_pixels'],1)
        self.assertEqual(result['per_texel_divisor_undershoot_pixels'],1)
        self.assertGreater(result['maximum_floor_undershoot'],0)
        bound=int(prepared[...,3].min())/255
        np.testing.assert_allclose(delivery.rgbd_decode(actual,bound)[0,0],(actual[0,0,:3]/255)**2.2/bound)

    def test_advanced_effective_constant_allows_raw_alpha_undershoot(self):
        master=masters(True);prepared,_=delivery.core.encode_rgbd(master,fixed_divisor=True)
        actual=prepared.copy();actual[0,0,3]=0
        result=delivery.volume_rgbd_comparison(actual,prepared,master,int(prepared[0,0,3])/255,True,thresholds())
        self.assertEqual(result['status'],'passed');self.assertFalse(result['raw_alpha_constant'])
        self.assertEqual(result['effective_divisor_max_error'],0)
        self.assertEqual([row['direction'] for row in result['banks']],volume.DIRECTION_ORDER)

    def test_advanced_alpha_overshoot_rejects_even_with_loose_hdr_limits(self):
        master=masters(True);prepared,_=delivery.core.encode_rgbd(master,fixed_divisor=True)
        actual=prepared.copy();actual[0,0,3]+=1
        result=delivery.volume_rgbd_comparison(actual,prepared,master,int(prepared[0,0,3])/255,True,thresholds(1000))
        self.assertEqual(result['status'],'failed');self.assertEqual(result['decoded_alpha_overshoot_pixels'],1)
        self.assertGreater(result['effective_divisor_max_error'],1e-12)

    def test_roundtrip_thresholds_bank_swap_and_nonfinite_reject(self):
        master=masters(True);prepared,_=delivery.core.encode_rgbd(master,fixed_divisor=True);bound=int(prepared[0,0,3])/255
        result=delivery.volume_rgbd_comparison(np.roll(prepared,128,axis=0),prepared,master,bound,True,thresholds(.001))
        self.assertEqual(result['status'],'failed');self.assertTrue(any('+X' in row for row in result['failures']))
        for bad in (thresholds(float('nan')),{},thresholds(True)):
            with self.assertRaises(ValueError):delivery.volume_rgbd_comparison(prepared,prepared,master,bound,True,bad)
        master[0,0,0]=float('nan')
        with self.assertRaises(ValueError):delivery.volume_rgbd_comparison(prepared,prepared,master,bound,True,thresholds())

    def test_exact_native_model_marker_metadata(self):
        record=native_receipt();simple=volume.payload(record,'d'*64,.1,1)
        simple['advanced']=volume.advanced_payload(record['advanced'],'e'*64,.1)
        model=dict(meshes=[dict(name='hasenv',metadata={volume.KEY:simple})])
        delivery.volume_model_contract(model,record,'d'*64,.1,'e'*64,.1)
        for field,value in (('strength',.5),('coordinateSpace','world'),('outsideBounds','clamp'),('minDivisor',.2)):
            altered=copy.deepcopy(model);altered['meshes'][0]['metadata'][volume.KEY][field]=value
            with self.assertRaises(ValueError):delivery.volume_model_contract(altered,record,'d'*64,.1,'e'*64,.1)
        for altered in (dict(transformNodes=model['meshes']),dict(meshes=[dict(model['meshes'][0],positions=[0,0,0])])):
            with self.assertRaises(ValueError):delivery.volume_model_contract(altered,record,'d'*64,.1,'e'*64,.1)

    def test_paired_artifact_route_seals_inputs_and_retains_official_operations(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture=ArtifactFixture(temporary);result=fixture.validate()
            self.assertEqual(result['status'],'passed');self.assertEqual(len(fixture.calls),5)
            self.assertTrue((Path(temporary)/'validated/volume-delivery-validation.json').is_file())
            for name in ('simple','advanced'):
                row=result['volumes'][name]
                self.assertEqual(row['container_profile']['transfer'],1)
                self.assertEqual(row['encoder_policy_receipt']['uastc_quality'],4)
                self.assertIn('not quality-level inference',row['encoder_policy_receipt']['proof'])
                self.assertEqual(row['official_operations'][1]['command'][1:8],['extract','--transcode','rgba8','--level','0','--raw',str(fixture.base/(volume.FILENAME if name=='simple' else volume.ADVANCED_FILENAME))])
                self.assertTrue(Path(row['decoded_linear']).is_file())
            for path,digest in result['inputs'].items():self.assertEqual(delivery.sha(path),digest)
            with self.assertRaisesRegex(ValueError,'new volume validation'):fixture.validate()

    def test_actual_decoded_overshoot_persists_failed_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture=ArtifactFixture(temporary);fixture.decoded[str(fixture.base/volume.ADVANCED_FILENAME)][0,0,3]+=1
            with self.assertRaisesRegex(ValueError,'Decoded volume validation failed'):fixture.validate()
            report=delivery.read_json(fixture.base/'validated/volume-delivery-validation.json')
            self.assertEqual(report['status'],'failed')
            self.assertEqual(report['volumes']['advanced']['comparison']['decoded_alpha_overshoot_pixels'],1)

    def test_stale_hash_bad_metadata_and_encoder_policy_reject_before_extraction(self):
        for fault in ('hash','metadata','quality','orientation','capture_orientation','prepared_flip'):
            with self.subTest(fault=fault),tempfile.TemporaryDirectory() as temporary:
                fixture=ArtifactFixture(temporary)
                if fault=='hash':(fixture.base/'world.exr').write_bytes(b'changed')
                elif fault=='metadata':
                    fixture.model['meshes'][0]['metadata'][volume.KEY]['advanced']['directionOrder'].reverse()
                    fixture.reseal('model','room.babylon',fixture.model)
                    fixture.report['delivery_files'][0]['sha256']=delivery.sha(fixture.base/'room.babylon')
                    fixture.reseal('conversion_report','conversion.json',fixture.report)
                elif fault=='quality':
                    fixture.report['environment_lighting_volume']['command'][11]='3'
                    fixture.reseal('conversion_report','conversion.json',fixture.report)
                elif fault=='orientation':
                    fixture.receipt['advanced']['directionOrder'].reverse();fixture.reseal('receipt','volume_receipt.json',fixture.receipt)
                elif fault=='capture_orientation':
                    path=fixture.base/'advanced_provenance.json';provenance=delivery.read_json(path)
                    provenance['cameraEulerBlender']=[0,0,0];fixture.write(path.name,provenance)
                    fixture.receipt['advanced']['provenanceSha256']=delivery.sha(path)
                    fixture.reseal('receipt','volume_receipt.json',fixture.receipt)
                else:
                    path=fixture.base/'simple.png'
                    Image.fromarray(delivery.png_rgba(path)[::-1],'RGBA').save(path)
                    fixture.items['simple']['prepared']=fixture.ref(path)
                    fixture.report['environment_lighting_volume']['metrics']['prepared_sha256']=delivery.sha(path)
                    fixture.reseal('conversion_report','conversion.json',fixture.report)
                with self.assertRaises(ValueError):fixture.validate()
                self.assertEqual(fixture.calls,[])

    def test_binary_transfer_and_atlas_dimensions_are_independent_of_receipt_flags(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'volume.ktx2';data=bytearray(container(128,768));path.write_bytes(data)
            delivery.inspect_ktx2(path,'rgbd',[128,768],False,codec='uastc')
            with self.assertRaises(ValueError):delivery.inspect_ktx2(path,'rgbd',[128,128],False,codec='uastc')
            data[104+14]=2;path.write_bytes(data)
            with self.assertRaises(ValueError):delivery.inspect_ktx2(path,'rgbd',[128,768],False,codec='uastc')

    def test_cli_addition_preserves_ordinary_validate_dispatch(self):
        for command,target in (('validate','validate_delivery'),('validate-volumes','validate_volumes')):
            with self.subTest(command=command),patch.object(sys,'argv',['decoded_delivery.py',command,'--recipe','r.json','--output','out','--ktx','ktx.exe']),patch.object(delivery,target) as called:
                delivery.main();called.assert_called_once_with(Path('r.json'),Path('out'),Path('ktx.exe'))


class VolumeImageReaderRegression(unittest.TestCase):
    def fake_oiio(self,pixels,names):
        state=dict(attributes={},closed=False)
        class Config:
            def attribute(self,key,value):state['attributes'][key]=value
        class Reader:
            def spec(self):
                height,width,channels=pixels.shape
                return types.SimpleNamespace(depth=1,x=0,y=0,z=0,full_width=width,full_height=height,
                    width=width,height=height,nchannels=channels,channelnames=names)
            def read_image(self,data_type):state['read_type']=data_type;return pixels
            def close(self):state['closed']=True
        def opened(path,config):state['path']=path;return Reader()
        module=types.SimpleNamespace(ImageSpec=Config,ImageInput=types.SimpleNamespace(open=opened),FLOAT='float',UINT8='uint8')
        return module,state

    def test_oiio_exr_uses_native_channel_names_float32_and_top_rows(self):
        pixels=np.array([[[4,2,1,1],[8,6,3,1]],[[16,12,5,1],[26.3827,14,7,1]]],np.float32)
        module,state=self.fake_oiio(pixels,['B','G','R','A'])
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'nonzero.exr';path.write_bytes(b'\x76\x2f\x31\x01fixture')
            with patch.dict(sys.modules,{'OpenImageIO':module}),patch.object(delivery,'_linear',side_effect=AssertionError('legacy reader used')):
                result=delivery.volume_linear(path)
        np.testing.assert_array_equal(result,pixels[...,[2,1,0]])
        self.assertEqual(result.dtype,np.float32);self.assertEqual(state['read_type'],'float')
        self.assertEqual(state['attributes'],{'oiio:UnassociatedAlpha':1,'oiio:reorient':0,'oiio:RawColor':1})
        self.assertTrue(state['closed'])

    def test_oiio_png_requests_unassociated_raw_uint8_divisor_codes(self):
        pixels=np.array([[[255,80,40,153],[200,180,90,166]]],np.uint8)
        module,state=self.fake_oiio(pixels,['R','G','B','A'])
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'rgbd.png';Image.fromarray(pixels,'RGBA').save(path)
            with patch.dict(sys.modules,{'OpenImageIO':module}),patch.object(delivery,'png_rgba',side_effect=AssertionError('legacy reader used')):
                result=delivery.volume_png_rgba(path)
        np.testing.assert_array_equal(result,pixels)
        self.assertEqual(state['read_type'],'uint8');self.assertEqual(state['attributes']['oiio:UnassociatedAlpha'],1)
        self.assertTrue(state['closed'])

    def test_reader_rejects_missing_named_rgb_nonfinite_and_non_rgba8_png(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'master.exr';path.write_bytes(b'\x76\x2f\x31\x01fixture')
            for names,pixels in ((['X','Y','Z'],np.ones((1,1,3),np.float32)),(['R','G','B'],np.full((1,1,3),np.nan,np.float32))):
                module,state=self.fake_oiio(pixels,names)
                with patch.dict(sys.modules,{'OpenImageIO':module}),self.assertRaises(ValueError):delivery.volume_linear(path)
                self.assertTrue(state['closed'])
            png=Path(temporary)/'rgb.png';Image.fromarray(np.ones((1,1,3),np.uint8),'RGB').save(png)
            with self.assertRaises(ValueError):delivery.volume_png_rgba(png)
            path.write_bytes(b'not an EXR')
            with self.assertRaises(ValueError):delivery.volume_linear(path)

    def test_no_oiio_falls_back_only_to_existing_readers(self):
        rgb=np.array([[[2,4,8]]],np.float32);rgba=np.array([[[255,80,40,153]]],np.uint8)
        with tempfile.TemporaryDirectory() as temporary:
            exr=Path(temporary)/'master.exr';exr.write_bytes(b'\x76\x2f\x31\x01fixture')
            png=Path(temporary)/'rgba.png';Image.fromarray(rgba,'RGBA').save(png)
            with patch.dict(sys.modules,{'OpenImageIO':None}),patch.object(delivery,'_linear',return_value=rgb) as legacy_exr,patch.object(delivery,'png_rgba',return_value=rgba) as legacy_png:
                np.testing.assert_array_equal(delivery.volume_linear(exr),rgb)
                np.testing.assert_array_equal(delivery.volume_png_rgba(png),rgba)
                legacy_exr.assert_called_once_with(exr);legacy_png.assert_called_once_with(png)


if __name__=='__main__':unittest.main()
