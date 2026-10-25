"""Pure public-spec endpoint fixtures; no encoder, KTX DLL or Blender jobs."""
import importlib.util
import os
from pathlib import Path
import random
import sys
import types
import unittest
from unittest.mock import patch

ROOT=Path(os.environ.get('BJS_PLUGIN_ROOT',Path(__file__).resolve().parents[1]/'src/babylon_js'))
package=types.ModuleType('alpha_test_package');package.__path__=[str(ROOT)];sys.modules[package.__name__]=package
def load(name):
    spec=importlib.util.spec_from_file_location(package.__name__+'.'+name,ROOT/(name+'.py'))
    value=importlib.util.module_from_spec(spec);sys.modules[spec.name]=value;spec.loader.exec_module(value);return value
alpha=load('uastc_alpha');volume=load('environment_lighting_volume')

# Independent fixture field declarations from the public specification.
FIELDS={9:(33,4,0,4,2),10:(20,4,1,4,1),11:(21,4,1,4,1),12:(20,6,1,4,1),
        13:(30,8,0,4,1),14:(28,8,0,4,1),15:(30,8,0,2,1),16:(34,8,0,2,2),17:(29,8,0,2,1)}
CODES=((1,4),(53,6),(29,5),(3,5),(19,5),(11,5),(27,5),(7,5),(23,5),(15,5),(2,3),(0,2),(6,3),(31,5),(13,5),(5,7),(21,6),(37,6),(9,4),(69,7))

def fixture(mode,values,seed=98):
    rng=random.Random(seed);value=rng.getrandbits(128)
    code,width=CODES[mode];value=(value & ~((1<<width)-1))|code
    if mode==8:return ((value & ~(255<<29)) | (values[0]<<29)).to_bytes(16,'little')
    start,bits,trits,components,subsets=FIELDS[mode]
    count=components*subsets*2;assert len(values)==count
    cursor=start;packed=0
    if trits:
        for first in range(0,count,5):
            group=values[first:first+5];width=(3**len(group)-1).bit_length()
            packed |= sum((q>>bits)*3**i for i,q in enumerate(group)) << (cursor-start);cursor+=width
    for i,q in enumerate(values):packed |= (q & ((1<<bits)-1)) << (cursor-start+i*bits)
    size=cursor-start+count*bits
    value=(value & ~(((1<<size)-1)<<start)) | (packed<<start)
    # Pattern0 is valid for every 2-subset mode; other header/hints stay varied.
    if mode==9:value &= ~(31<<28)
    if mode==16:value &= ~(31<<29)
    return value.to_bytes(16,'little')

def reference_values(block,mode):
    number=int.from_bytes(block,'little')
    start,bits,trits,components,subsets=FIELDS[mode];count=components*subsets*2
    offset=start;digits=[]
    if trits:
        for first in range(0,count,5):
            n=min(5,count-first);width=(3**n-1).bit_length();group=(number>>offset)&((1<<width)-1);offset+=width
            digits += [(group//(3**i))%3 for i in range(n)]
    endpoints=[((number>>(offset+i*bits))&((1<<bits)-1))+(digits[i]*(1<<bits) if trits else 0) for i in range(count)]
    return endpoints,((1<<(offset+count*bits-start))-1)<<start


class EndpointCeiling(unittest.TestCase):
    def test_official_unquant_golden_values(self):
        # Range13 encodes 11 at trit2,bits0; next representable above15 is16.
        expected={0:0,1:255,2:16,3:239,16:5,17:250,18:21,32:11,33:244,34:27}
        for q,value in expected.items():self.assertEqual(alpha._unquant(q,4,True),value)
        for q,value in {0:0,1:255,2:4,64:1,128:2,134:14,8:16}.items():
            self.assertEqual(alpha._unquant(q,6,True),value)
        self.assertEqual([alpha._unquant(i,4,False) for i in range(16)],list(range(0,256,17)))

    def test_all_alpha_modes_preserve_nonalpha_endpoints_fields_and_weights(self):
        for mode,(start,bits,trits,components,subsets) in FIELDS.items():
            levels=(3 if trits else 1)*(1<<bits)
            values=[(i*19+7)%levels for i in range(components*subsets*2)]
            indices=[s*components*2+(components-1)*2+i for s in range(subsets) for i in (0,1)]
            for i in indices:values[i]=1 if trits else levels-1
            original=fixture(mode,values)
            corrected,proof=alpha.ceiling_block(original,15)
            before,mask=reference_values(original,mode);after,unused=reference_values(corrected,mode)
            self.assertEqual(int.from_bytes(original,'little')&~mask,int.from_bytes(corrected,'little')&~mask)
            for i in range(len(values)):
                if i not in indices:self.assertEqual(after[i],before[i],(mode,i))
            self.assertTrue(all(v<=15 for v in proof['alpha_after']))
            self.assertEqual(proof['representable_ceiling'],{9:0,10:11,11:11,12:14}.get(mode,15))
            self.assertEqual(alpha.ceiling_block(corrected,15)[0],corrected)

    def test_shared_ETQ_repack_keeps_RGB_trits_and_bits(self):
        for mode in (10,11,12):
            bits=FIELDS[mode][1];values=[0,(1<<bits),2*(1<<bits),1,3,5,1,1]
            original=fixture(mode,values)
            corrected,proof=alpha.ceiling_block(original,15)
            after,unused=reference_values(corrected,mode)
            self.assertEqual(after[:6],values[:6])
            self.assertLessEqual(max(proof['alpha_after']),15)
            self.assertNotEqual(corrected,original)

    def test_divisor_thresholds_and_random_endpoint_fixtures(self):
        rng=random.Random(20261023)
        for mode,(start,bits,trits,components,subsets) in FIELDS.items():
            levels=(3 if trits else 1)*(1<<bits)
            for divisor in (1,2,5,11,14,15,16,47,128,254,255):
                for repeat in range(5):
                    values=[rng.randrange(levels) for unused in range(components*subsets*2)]
                    original=fixture(mode,values,repeat)
                    revised,proof=alpha.ceiling_block(original,divisor)
                    before,mask=reference_values(original,mode);after,unused=reference_values(revised,mode)
                    indices=[s*components*2+(components-1)*2+i for s in range(subsets) for i in (0,1)]
                    for i in range(len(values)):
                        if i not in indices:self.assertEqual(before[i],after[i])
                    self.assertEqual(int.from_bytes(original,'little')&~mask,int.from_bytes(revised,'little')&~mask)
                    self.assertLessEqual(max(proof['alpha_after']),divisor)
                    if max(proof['alpha_before'])<=divisor:self.assertEqual(original,revised)

    def test_solid_and_all_RGB_only_modes(self):
        solid=fixture(8,[16]);result,proof=alpha.ceiling_block(solid,15)
        self.assertEqual((int.from_bytes(result,'little')>>29)&255,15)
        self.assertEqual(int.from_bytes(result,'little')&~(255<<29),int.from_bytes(solid,'little')&~(255<<29))
        for mode in tuple(range(8))+(18,):
            code,width=CODES[mode];block=code.to_bytes(16,'little')
            self.assertEqual(alpha.ceiling_block(block,255)[0],block)
            with self.assertRaises(ValueError):alpha.ceiling_block(block,15)

    def test_reserved_invalid_partial_and_noncanonical_reject(self):
        for block,divisor in ((b'',15),(bytes(15),15),(bytes(16),True),(bytes(16),0),(bytes(16),256),(bytes(16),15.0),(CODES[19][0].to_bytes(16,'little'),15)):
            with self.assertRaises(ValueError):alpha.ceiling_block(block,divisor)
        # First 5 trits require code<243; 255 must fail rather than silently wrap.
        malformed=int.from_bytes(fixture(10,[0]*8),'little') | (255<<20)
        with self.assertRaises(ValueError):alpha.ceiling_block(malformed.to_bytes(16,'little'),15)
        for data in (b'',bytes(17)):
            with self.assertRaises(ValueError):alpha.ceiling_blocks(data,15)

    def test_block_report_and_source_immutability(self):
        original=fixture(10,[0]*6+[1,1])+fixture(8,[16])
        saved=bytes(original);changed,proof=alpha.ceiling_blocks(original,15)
        self.assertEqual(saved,original);self.assertEqual(proof['blocks'],2);self.assertEqual(proof['changed_blocks'],2)
        self.assertEqual(proof['modes'],{'10':1,'8':1});self.assertEqual(len(changed),32)

    def test_saved_policy_is_optional_and_defaults_are_unchanged(self):
        self.assertIsNone(volume.saved_options({}))
        basic={'bjs_environment_lighting_volume_config':'//receipt.json'}
        self.assertEqual(volume.saved_options(basic),dict(config='//receipt.json',image='',strength=1))
        basic['bjs_environment_lighting_volume_advanced_alpha_policy']='source-ceiling'
        self.assertEqual(volume.saved_options(basic)['advanced_alpha_policy'],'source-ceiling')

    def test_invalid_or_nonfixed_policy_rejects_before_prepare_encode(self):
        # Existing source-validation precondition is satisfied by a tiny owned
        # fixture path mock. No bpy, PNG, encoder or DLL is invoked here.
        core=load('ktx_conversion');ktx=load('ktx_export')
        with patch.object(Path,'is_file',return_value=True),patch.object(core,'sha',return_value='a'*64),patch.object(ktx,'prepare_blender') as prepare:
            for options in ({'advanced_alpha_policy':'other'}, {'advanced_alpha_policy':'source-ceiling','fixed_divisor':False}):
                with self.assertRaises(ValueError):volume._export_texture(Path('x.exr'),'a'*64,'x.ktx2',[128,768],Path('work'),Path('package'),'ktx.exe',8,**options)
            prepare.assert_not_called()


if __name__=='__main__':unittest.main()
