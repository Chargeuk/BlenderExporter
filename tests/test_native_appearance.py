"""Appearance-only native finishing proof; no Blender or conversion jobs."""
import copy,hashlib,json,struct,sys,tempfile,unittest,zlib
from pathlib import Path
from unittest.mock import patch
ENV=Path(__file__).resolve().parents[1]/'src/babylon_js/environment_tools';sys.path.insert(0,str(ENV))
import native_recombine as nr
from common import sha
import test_native_recombine


def png(path,size=512,colour_type=6):
    def chunk(name,data):return struct.pack('>I',len(data))+name+data+struct.pack('>I',zlib.crc32(name+data)&0xffffffff)
    channels=4 if colour_type==6 else 3
    path.write_bytes(b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',size,size,8,colour_type,0,0,0))+
        chunk(b'IDAT',zlib.compress((b'\0'+bytes([188,188,188,0][:channels])*size)*size))+chunk(b'IEND',b''))


class NativeAppearanceTests(unittest.TestCase):
    def fixture(self,root):
        c,original,tools,build=test_native_recombine.NativeRecombineTests().fixture(root)
        geometry='a'*64
        path=original/'geometry/result.json';path.write_text(json.dumps({'geometry_sha256':geometry}))
        nativepath=original/'bake/native_ownership.provenance.json';native=json.loads(nativepath.read_text());native.update(geometry_sha256=geometry,geometry_record_sha256=sha(path));nativepath.write_text(json.dumps(native))
        for rel in build['stages']['combine']['outputs']:build['stages']['combine']['outputs'][rel]=sha(original/rel)
        (original/'build.json').write_text(json.dumps(build))
        image=root/'appearance.png';png(image)
        settings=dict(schema_version=1,enabled=True,image=str(image),sha256=sha(image),size=512,colour_space='sRGB',alpha_mode='CHANNEL_PACKED',strength=1.0,uv='SimpleBake',uv_index=1,geometry_sha256=geometry)
        trial=root/'appearance-recipe.json';raw=json.loads(Path(c['_config']).read_text());raw[nr.APPEARANCE]=settings;trial.write_text(json.dumps(raw))
        c=dict(raw,_root=str(root),_source=c['_source'],_config=str(trial))
        return c,original,tools

    def rewrite(self,c):
        Path(c['_config']).write_text(json.dumps({k:v for k,v in c.items() if not k.startswith('_')}))

    def derived(self,root):
        c,original,tools=self.fixture(root);plan=nr.validate_original(c,original,tools);derived=root/'derived';nr.copy_verified(plan,derived)
        combined=derived/'masters/combined.exr';combined.write_bytes(b'evaluated uncoloured illumination')
        record=dict(schema_version=1,source_sha256=sha(c['_source']),combined_sha256=sha(combined),ownership_sha256=sha(derived/'ownership/labels.npy'),
            native_recombine_provenance_sha256=sha(derived/'native_recombine.provenance.json'),original_native_candidate=str(original),finishing_overrides={},
            appearance_colour_adjustment=nr.appearance_settings(c),appearance_only=True,illumination_colour_separate=True,preview_sha256='b'*64)
        sidecar=derived/'masters/combined.provenance.json';sidecar.write_text(json.dumps(record))
        build={'schema_version':1,'inputs':{str(Path(c['_config']).resolve()):sha(c['_config']),c['_source']:sha(c['_source'])},
            'stages':{'combine':{'outputs':{str(p.relative_to(derived)):sha(p) for p in (combined,sidecar)}}}}
        (derived/'build.json').write_text(json.dumps(build))
        return c,original,tools,derived

    def test_missing_or_disabled_map_keeps_original_no_overlay_behavior(self):
        self.assertIsNone(nr.appearance_settings({}))
        disabled={nr.APPEARANCE:{'schema_version':1,'enabled':False}}
        self.assertIsNone(nr.appearance_settings(disabled));self.assertIsNone(nr.apply_finishing(disabled))
        with self.assertRaises(ValueError):nr.appearance_settings({nr.APPEARANCE:None})
        with self.assertRaises(ValueError):nr.appearance_settings({nr.APPEARANCE:dict(disabled[nr.APPEARANCE],strength=1)})

    def test_enabled_map_binds_separately_and_copies_native_exact(self):
        with tempfile.TemporaryDirectory() as tmp:
            c,original,tools=self.fixture(Path(tmp));plan=nr.validate_original(c,original,tools)
            self.assertEqual(plan[nr.APPEARANCE]['size'],512);self.assertTrue(plan['physical_source_unchanged'])
            self.assertNotIn(c[nr.APPEARANCE]['image'],json.loads(Path(plan['original_config']).read_text()).get('source_images',[]))
            derived=Path(tmp)/'derived';nr.copy_verified(plan,derived);self.assertEqual(nr.verify_copied(c,derived),plan)
            self.assertEqual(sha(derived/nr.APPEARANCE_COPY),c[nr.APPEARANCE]['sha256'])
            for name in plan['copies']:self.assertEqual(sha(derived/name),sha(original/name))

    def test_bad_hash_interpretation_strength_or_uv_is_rejected(self):
        variants=[('sha256','0'*64),('size',1024),('size',True),('colour_space','Non-Color'),('alpha_mode','STRAIGHT'),
            ('strength',True),('strength',float('nan')),('strength',float('inf')),('strength',-.1),('strength',1.1),('uv','UVMap'),('uv_index',0),('uv_index',True),('geometry_sha256','bad')]
        for field,value in variants:
            with self.subTest(field=field,value=value),tempfile.TemporaryDirectory() as tmp:
                c,original,tools=self.fixture(Path(tmp));c[nr.APPEARANCE][field]=value;self.rewrite(c)
                with self.assertRaises(ValueError):nr.validate_original(c,original,tools)

    def test_actual_map_dimensions_rgba_and_geometry_are_checked(self):
        for size,ctype in ((256,6),(512,2)):
            with self.subTest(size=size,ctype=ctype),tempfile.TemporaryDirectory() as tmp:
                c,original,tools=self.fixture(Path(tmp));p=Path(c[nr.APPEARANCE]['image']);png(p,size,ctype);c[nr.APPEARANCE]['sha256']=sha(p);self.rewrite(c)
                with self.assertRaisesRegex(ValueError,'RGBA8'):nr.validate_original(c,original,tools)
        with tempfile.TemporaryDirectory() as tmp:
            c,original,tools=self.fixture(Path(tmp));c[nr.APPEARANCE]['geometry_sha256']='c'*64;self.rewrite(c)
            with self.assertRaisesRegex(ValueError,'geometry/UV2'):nr.validate_original(c,original,tools)

    def test_map_cannot_be_a_physical_asset_or_stack_with_existing_tint(self):
        with tempfile.TemporaryDirectory() as tmp:
            c,_,_=self.fixture(Path(tmp));c['source_images']=[c[nr.APPEARANCE]['image']]
            with self.assertRaisesRegex(ValueError,'outside physical'):nr.appearance_settings(c)
        for other in ({'physical_colour_adjustment':{'enabled':True}},{'finishing_overrides':{'Colour Adjustment Strength':1}}):
            with tempfile.TemporaryDirectory() as tmp:
                c,_,_=self.fixture(Path(tmp));c.update(other)
                with self.assertRaisesRegex(ValueError,'cannot stack'):nr.appearance_settings(c)

    def test_physical_recipe_and_saved_source_cannot_change(self):
        for field,value in [('receiver_response',{'enabled':True}),('physical_colour_adjustment',{'enabled':False}),('bake_lights',{'names':['Changed']}),('samples',{'DIRECT':1,'INDIRECT':1})]:
            with self.subTest(field=field),tempfile.TemporaryDirectory() as tmp:
                c,original,tools=self.fixture(Path(tmp));c[field]=value;self.rewrite(c)
                with self.assertRaisesRegex(ValueError,'physical recipe'):nr.validate_original(c,original,tools)
        with tempfile.TemporaryDirectory() as tmp:
            c,original,tools=self.fixture(Path(tmp));p=Path(tmp)/'other.blend';p.write_bytes(b'different source');c['_source']=str(p)
            with self.assertRaisesRegex(ValueError,'exact immutable'):nr.validate_original(c,original,tools)

    def test_copied_map_and_native_maps_cannot_change(self):
        for relative in (nr.APPEARANCE_COPY,'masters/direct_final.exr','masters/island_ids.exr'):
            with self.subTest(relative=relative),tempfile.TemporaryDirectory() as tmp:
                c,original,tools=self.fixture(Path(tmp));plan=nr.validate_original(c,original,tools);derived=Path(tmp)/'derived';nr.copy_verified(plan,derived);(derived/relative).write_bytes(b'changed')
                with self.assertRaises(ValueError):nr.verify_copied(c,derived)

    def test_public_derived_verifier_keeps_original_authority_and_new_appearance(self):
        with tempfile.TemporaryDirectory() as tmp:
            c,original,tools,derived=self.derived(Path(tmp));proof=nr.verify_derived(c,derived,tools)
            self.assertEqual(proof['kind'],'verified-native-appearance-derivative');self.assertEqual(proof['original_candidate'],str(original))
            self.assertEqual(proof['source_sha256'],sha(c['_source']));self.assertIn(str(derived/'masters/combined.exr'),proof['inputs'])
            self.assertEqual(proof[nr.APPEARANCE]['sha256'],c[nr.APPEARANCE]['sha256'])

    def test_incomplete_stale_combination_and_unsafe_chain_are_rejected(self):
        for fault in ('missing_combine','wrong_appearance','wrong_source','changed_master','changed_tool','chain'):
            with self.subTest(fault=fault),tempfile.TemporaryDirectory() as tmp:
                c,original,tools,derived=self.derived(Path(tmp));buildpath=derived/'build.json';build=json.loads(buildpath.read_text())
                if fault=='missing_combine':build['stages']={};buildpath.write_text(json.dumps(build))
                elif fault in ('wrong_appearance','wrong_source'):
                    p=derived/'masters/combined.provenance.json';d=json.loads(p.read_text());d['appearance_only' if fault=='wrong_appearance' else 'source_sha256']=False;p.write_text(json.dumps(d));build['stages']['combine']['outputs'][str(p.relative_to(derived))]=sha(p);buildpath.write_text(json.dumps(build))
                elif fault=='changed_master':(derived/'masters/combined.exr').write_bytes(b'changed')
                elif fault=='changed_tool':(tools/'src/babylon_js/environment_tools/worker.py').write_bytes(b'changed')
                else:(original/'native_recombine.provenance.json').write_text('{}')
                with self.assertRaises((ValueError,FileNotFoundError)):nr.verify_derived(c,derived,tools)


    def test_existing_scalar_and_immutable_physical_tint_strength_contract_stays_strict(self):
        values={'Direct Strength':1,'Indirect Strength':1,'Shadow Lift':0,'AO Strength':.15,
            'AO Contrast':1,'AO Direct Influence':0,'AO Indirect Influence':1}
        self.assertEqual(nr.finishing_overrides({'ao':{'enabled':True},'finishing_overrides':values}),values)
        old={'physical_colour_adjustment':{'enabled':True},'finishing_overrides':{'Colour Adjustment Strength':.5}}
        self.assertEqual(nr.finishing_overrides(old),old['finishing_overrides'])
        for bad in ({'Unknown':1},{'AO Strength':.15},{'Direct Strength':True},{'Indirect Strength':float('nan')}):
            with self.subTest(bad=bad),self.assertRaises(ValueError):nr.finishing_overrides({'finishing_overrides':bad})


if __name__=='__main__':unittest.main()
