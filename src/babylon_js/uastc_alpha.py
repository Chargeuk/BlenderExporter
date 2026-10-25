"""Opt-in fixed-RGBD alpha ceiling for native RGBA8 volume reconstruction.

The public-domain UASTC LDR specification defines these endpoint fields/BISE:
https://github.com/BinomialLLC/basis_universal/wiki/UASTC-LDR-4x4-Texture-Specification
No weights, RGB endpoints, modes, partitions or transcode hints are changed.
ETC2 alpha hints are retained: this policy makes NO ETC/BC hardware guarantee.
"""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess


MODE_CODES = ((1,4),(0x35,6),(0x1d,5),(3,5),(0x13,5),(0xb,5),(0x1b,5),
              (7,5),(0x17,5),(0xf,5),(2,3),(0,2),(6,3),(0x1f,5),(0xd,5),
              (5,7),(0x15,6),(0x25,6),(9,4),(0x45,7))
# mode: (first ETQ/EBITS bit, low bits per endpoint, trits, components, subsets)
_LAYOUT = {9:(33,4,False,4,2),10:(20,4,True,4,1),11:(21,4,True,4,1),
           12:(20,6,True,4,1),13:(30,8,False,4,1),14:(28,8,False,4,1),
           15:(30,8,False,2,1),16:(34,8,False,2,2),17:(29,8,False,2,1)}
_SPEC = 'https://github.com/BinomialLLC/basis_universal/wiki/UASTC-LDR-4x4-Texture-Specification'


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _mode(value):
    for mode, (code, width) in enumerate(MODE_CODES):
        if value & ((1 << width)-1) == code:
            if mode == 19: raise ValueError('Reserved UASTC mode19')
            return mode
    raise ValueError('Invalid UASTC mode')


def _unquant(value, bits, trits):
    if not trits:
        if bits == 8: return value
        if bits == 4: return value * 17
        raise ValueError('Unsupported pure endpoint range')
    # ASTC endpoint unquantization, range13 or19. UASTC stores the trits
    # as direct base3 digits, not ASTC's interleaved/remapped ETQ codes.
    low = value & ((1 << bits)-1)
    digit = value >> bits
    if digit > 2: raise ValueError('Invalid endpoint trit')
    pattern, factor = {4:('dcb000dcb',22),6:('fedcb000f',5)}[bits]
    spread = 0
    for character in pattern:
        spread = (spread << 1) | (0 if character == '0' else (low >> (ord(character)-97)) & 1)
    sign = 511 if low & 1 else 0
    return (sign & 128) | (((digit * factor + spread) ^ sign) >> 2)


def _endpoints(value, mode):
    start,bits,trits,components,subsets = _LAYOUT[mode]
    count = components * subsets * 2
    cursor = start
    digits = []
    if trits:
        for offset in range(0,count,5):
            n = min(5,count-offset)
            width = (3**n-1).bit_length()
            packed = (value >> cursor) & ((1 << width)-1)
            if packed >= 3**n: raise ValueError('Noncanonical UASTC endpoint trit group')
            cursor += width
            for unused in range(n):
                digits.append(packed % 3);packed //= 3
    low_start = cursor
    quantized = [((value >> (low_start+i*bits)) & ((1 << bits)-1)) |
                 ((digits[i] << bits) if trits else 0) for i in range(count)]
    return quantized,(start,low_start,bits,trits,components,subsets)


def _write_endpoints(value, quantized, layout):
    start,low_start,bits,trits,components,subsets = layout
    count = len(quantized)
    width = low_start + count*bits-start
    result = value & ~(((1 << width)-1) << start)
    cursor = start
    if trits:
        for offset in range(0,count,5):
            group=quantized[offset:offset+5]
            packed=sum((v >> bits)*3**i for i,v in enumerate(group))
            result |= packed << cursor
            cursor += (3**len(group)-1).bit_length()
    for i,q in enumerate(quantized):result |= (q & ((1 << bits)-1)) << (low_start+i*bits)
    return result


def ceiling_block(block, divisor):
    """Return one constrained block and proof; no block/container transcoding."""
    if len(block) != 16 or type(divisor) is not int or not 1 <= divisor <= 255:
        raise ValueError('Expected 16 UASTC bytes and original divisor byte1..255')
    value=int.from_bytes(block,'little');mode=_mode(value)
    if mode == 8:
        before=(value >> 29) & 255
        after=min(before,divisor)
        updated=(value & ~(255 << 29)) | (after << 29)
        info=dict(mode=mode,range_levels=256,representable_ceiling=divisor,
                  alpha_before=[before],alpha_after=[after])
    elif mode in _LAYOUT:
        endpoints,layout=_endpoints(value,mode)
        start,low_start,bits,trits,components,subsets=layout
        indices=[s*components*2+(components-1)*2+i for s in range(subsets) for i in (0,1)]
        levels=(3 if trits else 1)*(1 << bits)
        allowed=[q for q in range(levels) if _unquant(q,bits,trits) <= divisor]
        ceiling=max(_unquant(q,bits,trits) for q in allowed)
        chosen=next(q for q in allowed if _unquant(q,bits,trits)==ceiling)
        revised=list(endpoints)
        for i in indices:
            if _unquant(revised[i],bits,trits)>divisor:revised[i]=chosen
        updated=_write_endpoints(value,revised,layout)
        check,unused=_endpoints(updated,mode)
        if any(check[i]!=endpoints[i] for i in range(len(check)) if i not in indices):
            raise ValueError('Nonalpha UASTC endpoint changed')
        mask=((1 << (low_start+len(endpoints)*bits-start))-1) << start
        if (updated & ~mask)!=(value & ~mask):raise ValueError('Nonendpoint UASTC fields changed')
        info=dict(mode=mode,range_levels=levels,representable_ceiling=ceiling,
                  alpha_before=[_unquant(endpoints[i],bits,trits) for i in indices],
                  alpha_after=[_unquant(check[i],bits,trits) for i in indices])
    else:
        # RGB-only UASTC implicitly decodes alpha255. Accept only originalD255.
        if divisor != 255:raise ValueError('RGB-only UASTC mode cannot satisfy source alpha ceiling')
        updated=value;info=dict(mode=mode,range_levels=None,representable_ceiling=255,alpha_before=[255],alpha_after=[255])
    if max(info['alpha_after'])>divisor:raise ValueError('UASTC endpoint alpha ceiling not satisfied')
    result=updated.to_bytes(16,'little')
    info['changed']=result!=block
    return result,info


def ceiling_blocks(data, divisor):
    if not data or len(data)%16:raise ValueError('Expected whole nonempty UASTC blocks')
    output=bytearray();modes={};changes=[];ceilings={}
    for i in range(0,len(data),16):
        block,proof=ceiling_block(data[i:i+16],divisor)
        output.extend(block);mode=str(proof['mode'])
        modes[mode]=modes.get(mode,0)+1;ceilings[mode]=proof['representable_ceiling']
        if proof['changed']:changes.append(dict(block=i//16,**proof))
    return bytes(output),dict(blocks=len(data)//16,changed_blocks=len(changes),modes=modes,
                              representable_ceilings=ceilings,changes=changes)


def _patch_container(source, target, divisor, ktx, expected_size):
    """Public libktx4.4.2 APIs inflate, expose UASTC, deflate3, and serialize."""
    if os.name!='nt' or struct.calcsize('P')!=8:
        raise ValueError('Source-ceiling currently requires verified Windows64 libktx')
    dll=Path(ktx).resolve().parent/'ktx.dll'
    if not dll.is_file():raise ValueError('libktx must be beside the provided KTX executable')
    handle=os.add_dll_directory(str(dll.parent))
    texture=ctypes.c_void_p()
    try:
        lib=ctypes.WinDLL(str(dll))
        signatures={
            'ktxTexture2_CreateFromNamedFile':([ctypes.c_char_p,ctypes.c_uint32,ctypes.POINTER(ctypes.c_void_p)],ctypes.c_int),
            'ktxTexture_GetData':([ctypes.c_void_p],ctypes.c_void_p),
            'ktxTexture_GetDataSize':([ctypes.c_void_p],ctypes.c_size_t),
            'ktxTexture2_DeflateZstd':([ctypes.c_void_p,ctypes.c_uint32],ctypes.c_int),
            'ktxTexture2_WriteToNamedFile':([ctypes.c_void_p,ctypes.c_char_p],ctypes.c_int),
            'ktxTexture2_Destroy':([ctypes.c_void_p],None)}
        for name,(args,result) in signatures.items():
            function=getattr(lib,name);function.argtypes=args;function.restype=result
        def checked(name,*args):
            code=getattr(lib,name)(*args)
            if code:raise RuntimeError(name+' failed: '+str(code))
            return code
        checked('ktxTexture2_CreateFromNamedFile',str(source).encode('utf8'),1,ctypes.byref(texture))
        # LOAD_IMAGE_DATA_BIT inflates Zstd and updates libktx's own level index.
        pointer=lib.ktxTexture_GetData(texture);size=lib.ktxTexture_GetDataSize(texture)
        if not pointer or size!=expected_size:raise ValueError('Unsupported inflated UASTC layout/size')
        original=ctypes.string_at(pointer,size)
        revised,proof=ceiling_blocks(original,divisor)
        ctypes.memmove(pointer,revised,size)
        checked('ktxTexture2_DeflateZstd',texture,3)
        checked('ktxTexture2_WriteToNamedFile',texture,str(target).encode('utf8'))
        proof.update(libktx=str(dll),libktx_sha256=_sha(dll),
                     UASTC_before_sha256=hashlib.sha256(original).hexdigest(),UASTC_after_sha256=hashlib.sha256(revised).hexdigest())
        return proof,size
    finally:
        if texture.value:lib.ktxTexture2_Destroy(texture)
        handle.close()


def read_fixed_preparation(prepared, divisor):
    """Read PNG data without alpha association; reject a nonconstant source D."""
    import numpy as np
    import OpenImageIO as oiio
    if type(divisor) is not int or not 1<=divisor<=255:raise ValueError('Original prepared divisor byte required')
    prepared=Path(prepared)
    header=prepared.read_bytes()[:29]
    if header[:8]!=b'\x89PNG\r\n\x1a\n' or header[24:26]!=bytes((8,6)):
        raise ValueError('Source-ceiling requires actual prepared PNG RGBA8')
    config=oiio.ImageSpec();config.attribute('oiio:UnassociatedAlpha',1);config.attribute('oiio:reorient',0)
    image=oiio.ImageBuf(str(prepared),0,0,config);pixels=image.get_pixels(oiio.UINT8)
    if str(image.spec().format)!='uint8' or pixels.ndim!=3 or pixels.shape[2]!=4 or not np.all(pixels[...,3]==divisor):
        raise ValueError('Prepared alpha must be exactly the original fixed divisor everywhere')
    height,width=pixels.shape[:2]
    if width%4 or height%4:raise ValueError('Whole UASTC 4x4 block dimensions required')
    return pixels


def enforce_source_ceiling(source, target, *, prepared, divisor, ktx):
    """Preserve original file; fail closed unless actual RGBA8 RGB stays exact.

    This policy is only for fixed-divisor volumes reconstructed to nativeRGBA8.
    It does not qualify other GPU transcodes, correct lossy RGB, or change D.
    """
    import numpy as np
    from .ktx_export import process_options
    from .workflow_tools.decoded_delivery import inspect_ktx2
    source,target,prepared=map(Path,(source,target,prepared))
    if os.name!='nt' or struct.calcsize('P')!=8:raise ValueError('Verified Windows64 libktx required')
    if target.exists():raise ValueError('Corrected target must be fresh; preserve original KTX')
    report_path=target.with_suffix('.alpha-ceiling.json')
    if report_path.exists():raise ValueError('Alpha policy report must be fresh')
    pixels=read_fixed_preparation(prepared,divisor);height,width=pixels.shape[:2]
    profile=inspect_ktx2(source,'rgbd',[width,height],False,codec='uastc')
    proof=dict(status='FAILED_PRESERVE_ALL_ARTIFACTS',policy='source-ceiling',specification=_SPEC,
               original=str(source),original_sha256=_sha(source),target=str(target),prepared=str(prepared),prepared_sha256=_sha(prepared),
               original_divisor_byte=divisor,minimum_divisor=divisor/255,profile_before=profile,
               scope='Fixed-divisor nativeRGBA8 volume only; ETC2 hints retained; no ETC/BC hardware equivalence',calls=[])
    original_hash=proof['original_sha256']
    def run(argv):
        result=subprocess.run(argv,capture_output=True,text=True,**process_options())
        proof['calls'].append(dict(argv=list(map(str,argv)),numeric_exit=result.returncode,stdout=result.stdout,stderr=result.stderr))
        if result.returncode:raise RuntimeError('Source-ceiling KTX command failed: '+result.stderr)
    def decode(path,suffix):
        raw=target.with_name(target.stem+'.'+suffix+'.rgba')
        if raw.exists():raise ValueError('Native RGBA proof output must be fresh')
        run([str(ktx),'extract','--transcode','rgba8','--level','0','--raw',str(path),str(raw)])
        data=np.fromfile(raw,dtype=np.uint8)
        if data.size!=width*height*4:raise ValueError('Unexpected nativeRGBA8 extraction size')
        return data.reshape(height,width,4),raw
    try:
        version=subprocess.run([str(ktx),'--version'],capture_output=True,text=True,**process_options())
        proof['calls'].append(dict(argv=[str(ktx),'--version'],numeric_exit=version.returncode,stdout=version.stdout,stderr=version.stderr))
        if version.returncode or '4.4.2' not in version.stdout:raise ValueError('Verified libktx/KTX4.4.2 required')
        before,before_path=decode(source,'before')
        patch,size=_patch_container(source,target,divisor,ktx,(width//4)*(height//4)*16)
        proof['endpoint_patch']=patch
        run([str(ktx),'validate',str(target)])
        after_profile=inspect_ktx2(target,'rgbd',[width,height],False,codec='uastc')
        after,after_path=decode(target,'after')
        proof.update(target_sha256=_sha(target),profile_after=after_profile,
            before_raw=str(before_path),before_raw_sha256=_sha(before_path),after_raw=str(after_path),after_raw_sha256=_sha(after_path),
            RGB_byte_exact=bool(np.array_equal(before[...,:3],after[...,:3])),
            RGB_before_sha256=hashlib.sha256(before[...,:3].tobytes()).hexdigest(),RGB_after_sha256=hashlib.sha256(after[...,:3].tobytes()).hexdigest(),
            alpha_before_range=[int(before[...,3].min()),int(before[...,3].max())],alpha_after_range=[int(after[...,3].min()),int(after[...,3].max())],
            alpha_above_source_ceiling=int(np.count_nonzero(after[...,3]>divisor)),
            effective_divisor_constant=bool(np.all(np.maximum(after[...,3],divisor)==divisor)))
        if not proof['RGB_byte_exact'] or proof['alpha_above_source_ceiling'] or not proof['effective_divisor_constant']:
            raise ValueError('Actual nativeRGBA8 RGB/alpha policy proof failed')
        if _sha(source)!=original_hash or _sha(prepared)!=proof['prepared_sha256']:
            raise ValueError('Original KTX or prepared source changed')
        proof['status']='NATIVE_RGBA8_ALPHA_CEILING_VERIFIED;HDR_QUALITY_SEPARATE'
        return proof
    except BaseException as error:
        proof['error']=dict(type=type(error).__name__,message=str(error))
        raise
    finally:
        report_path.write_text(json.dumps(proof,indent=2,allow_nan=False)+'\n',encoding='utf8')
