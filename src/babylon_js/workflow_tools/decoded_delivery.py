"""Offline compressed delivery diagnostics and disposable matched diffuse views."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import struct
import subprocess
import sys
from importlib.util import spec_from_file_location,module_from_spec
import numpy as np

HERE=Path(__file__).resolve().parent
# Standalone callers must not expose babylon_js/logging.py as stdlib logging,
# nor import the Blender package (which requires bpy). Load only the core file.
_core_spec=spec_from_file_location('bjs_decoded_delivery_ktx_conversion',HERE.parent/'ktx_conversion.py')
core=module_from_spec(_core_spec);_core_spec.loader.exec_module(core)
sys.path.insert(0,str(HERE))
from uv_tint_filter import bilinear,diagnose,decode_rgba8,effective_multiplier


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def read_json(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save(path,value):Path(path).write_text(json.dumps(value,indent=2,allow_nan=False),encoding='utf8')


def inspect_ktx2(path,role,dimensions,mipmaps=False,*,codec=None):
    """Basic descriptor/container profile; official ktx validate remains mandatory."""
    data=Path(path).read_bytes()
    if len(data)<104 or data[:12]!=b'\xabKTX 20\xbb\r\n\x1a\n':raise ValueError('Expected KTX2 container')
    fields=struct.unpack_from('<13I2Q',data,12)
    vk,typesize,width,height,depth,layers,faces,levels,compression,dfdo,dfdl,kvdo,kvdl,sgdo,sgdl=fields
    if (typesize!=1 or [width,height]!=list(dimensions) or not 1<=width<=8192 or not 1<=height<=8192
            or depth!=0 or layers!=0 or faces!=1 or vk!=0):
        raise ValueError('Expected nonarray 2D transcoded delivery at explicit dimensions')
    if not isinstance(mipmaps,bool):raise ValueError('Explicit boolean conversion-record mipmaps policy required')
    if role in ('rgbd','tint') and mipmaps:raise ValueError('RGBD/tint require one level and mipmaps false')
    expected_levels=math.floor(math.log2(max(width,height)))+1 if mipmaps else 1
    if levels!=expected_levels:raise ValueError('KTX2 level count differs from declared complete mipmaps policy')
    index_end=80+24*levels
    if len(data)<index_end:raise ValueError('Truncated KTX2 level index')
    def section(offset,length):
        if offset<index_end or length<0 or offset+length>len(data):raise ValueError('KTX2 section bounds invalid')
        return data[offset:offset+length]
    dfd=section(dfdo,dfdl)
    if len(dfd)<44 or struct.unpack_from('<I',dfd)[0]!=dfdl:raise ValueError('KTX2 basic DFD invalid')
    vendor,version,block=struct.unpack_from('<IHH',dfd,4)
    if vendor!=0 or version!=2 or block+4!=dfdl or block<40 or (block-24)%16:
        raise ValueError('KTX2 requires one Khronos basic DFD v2')
    model,primaries,transfer,flags=struct.unpack_from('<4B',dfd,12)
    channels=[dfd[28+i*16+3]&15 for i in range((block-24)//16)]
    if flags&1:raise ValueError('Premultiplied delivery channels are unsupported')
    if role=='rgbd':
        if codec not in (None,'uastc'):raise ValueError('RGBD conversion record requires uastc')
        expected=(166,1,2);alpha=3 in channels;declared_codec='uastc'
    elif role=='tint':
        declared_codec='basis-lz' if codec is None else codec
        if declared_codec not in ('basis-lz','uastc'):raise ValueError('Tint conversion codec must be basis-lz or uastc')
        if declared_codec=='uastc':expected=(166,2,2);alpha=3 in channels
        else:expected=(163,2,1);alpha=15 in channels
    elif role in ('colour-atlas','material-linear'):
        if codec not in (None,'basis-lz'):raise ValueError('Material atlas conversion record requires basis-lz')
        expected=(163,2 if role=='colour-atlas' else 1,1);alpha=15 in channels;declared_codec='basis-lz'
    else:raise ValueError('Expected rgbd, tint, colour-atlas or material-linear role')
    if (model,transfer,compression)!=expected or primaries!=1:
        raise ValueError('KTX2 codec/transfer/primaries differ from delivery role')
    if role in ('rgbd','tint') and not alpha:raise ValueError('Delivery requires encoded alpha channel')
    level_records=[];payloads=[]
    metadata=[(offset,offset+length) for offset,length in ((dfdo,dfdl),(kvdo,kvdl),(sgdo,sgdl)) if length]
    for level in range(levels):
        offset,length,uncompressed=struct.unpack_from('<3Q',data,80+24*level)
        section(offset,length)
        if not length:raise ValueError('Empty KTX2 level')
        interval=(offset,offset+length)
        if any(max(interval[0],other[0])<min(interval[1],other[1]) for other in payloads+metadata):
            raise ValueError('Overlapping KTX2 level payload or metadata')
        payloads.append(interval)
        level_records.append({'level':level,'dimensions':[max(1,width>>level),max(1,height>>level)],
                              'offset':offset,'bytes':length,'uncompressed_bytes':uncompressed})
    if sgdl:section(sgdo,sgdl)
    entries={};cursor=0;kvd=section(kvdo,kvdl) if kvdl else b''
    while cursor<len(kvd):
        if cursor+4>len(kvd):raise ValueError('Truncated KTX2 key/value entry')
        length=struct.unpack_from('<I',kvd,cursor)[0];cursor+=4
        value=kvd[cursor:cursor+length]
        if not length or len(value)!=length or b'\0' not in value:raise ValueError('Invalid KTX2 key/value entry')
        key,value=value.split(b'\0',1);key=key.decode('utf8')
        if key in entries:raise ValueError('Duplicate KTX2 key')
        entries[key]=value.rstrip(b'\0').decode('utf8',errors='replace');cursor+=(length+3)//4*4
    orientation=entries.get('KTXorientation','rd')
    if orientation!='rd':raise ValueError('Only standard rd container orientation supported; no inferred flips')
    return {'dimensions':[width,height],'levels':levels,'declared_mipmaps':mipmaps,'declared_codec':declared_codec,'level_payloads':level_records,
            'faces':faces,'vk_format':vk,'color_model':model,
            'transfer':transfer,'primaries':primaries,'supercompression':compression,'channels':channels,
            'alpha_present':alpha,'alpha_mode':'straight','container_orientation':orientation,
            'container_orientation_defaulted':'KTXorientation' not in entries,'keys':entries}


def png_rgba(path):
    from PIL import Image
    header=Path(path).read_bytes()[:29]
    if not header.startswith(b'\x89PNG\r\n\x1a\n') or header[24]!=8:raise ValueError('Expected actual PNG8 preparation')
    with Image.open(path) as image:
        if image.mode not in ('RGB','RGBA'):raise ValueError('Expected RGB/RGBA preparation; indexed PNG rejected')
        return np.asarray(image.convert('RGBA'),dtype=np.uint8)


def top_left(array,origin):
    if origin not in ('top-left','bottom-left'):raise ValueError('Explicit logical image origin required')
    return array if origin=='top-left' else array[::-1].copy()


def rgbd_decode(rgba,min_divisor):
    if rgba.dtype!=np.uint8 or rgba.ndim!=3 or rgba.shape[2]!=4:raise ValueError('RGBD decode expects RGBA8')
    if isinstance(min_divisor,bool) or not isinstance(min_divisor,(int,float)) or not math.isfinite(min_divisor) or not 0<min_divisor<=1:
        raise ValueError('Actual model minimum divisor must be finite in (0,1]')
    values=rgba.astype(np.float64)/255
    return values[...,:3]**2.2/np.maximum(values[...,3:4],min_divisor)


def sample_rgbd(rgba,uv,min_divisor,origin):
    # RGBD is UNORM: filter ENCODED RGB and divisor together, then reconstruct.
    if rgba.dtype!=np.uint8:raise ValueError('Expected encoded RGBA8')
    rgbd_decode(rgba,min_divisor)
    values=bilinear(rgba.astype(np.float64)/255,np.asarray(uv),image_origin=origin)
    return values[:,:3]**2.2/np.maximum(values[:,3:4],min_divisor)


def metrics(error):
    values=np.asarray(error,dtype=np.float64)
    if not values.size or not np.isfinite(values).all():raise ValueError('Error metric needs finite owned samples')
    return {'mean':float(values.mean()),'p95':float(np.percentile(values,95)),'maximum':float(values.max())}


def compare_rgbd(decoded,reference,master,owned,albedo,min_divisor):
    if (decoded.shape!=reference.shape or master.shape!=decoded.shape[:2]+(3,) or albedo.shape!=master.shape
            or owned.shape!=decoded.shape[:2] or owned.dtype!=bool or not owned.any()
            or not np.isfinite(master).all() or not np.isfinite(albedo).all() or (master<0).any() or (albedo<0).any()):
        raise ValueError('RGBD comparison requires matching nonnegative linear master/albedo and nonempty boolean owned mask')
    actual=rgbd_decode(decoded,min_divisor);prepared=rgbd_decode(reference,min_divisor)
    shaded_actual=np.power(np.maximum(actual*albedo,0),1/2.2)
    shaded_master=np.power(np.maximum(master*albedo,0),1/2.2)
    return {'owned_pixels':int(owned.sum()),'reconstructed_linear_vs_master':metrics(np.abs(actual-master)[owned]),
        'codec_linear_vs_prepared':metrics(np.abs(actual-prepared)[owned]),
        'preparation_linear_vs_master':metrics(np.abs(prepared-master)[owned]),
        'shaded_gamma22_vs_master':metrics(np.abs(shaded_actual-shaded_master)[owned]),
        'master_peak':float(master[owned].max()),'decoded_peak':float(actual[owned].max()),
        'prepared_alpha_range':[int(reference[...,3].min()),int(reference[...,3].max())],
        'decoded_alpha_range':[int(decoded[...,3].min()),int(decoded[...,3].max())],
        'decoded_alpha_below_model_bound_pixels':int(np.count_nonzero(decoded[...,3]/255<min_divisor)),
        'alpha_semantics':'RGBD divisor, never opacity/influence','minimum_divisor':min_divisor,
        'limits':'Base-level texel numeric comparison only; shaded values are local diffuse gamma2.2 reconstruction, not KaDshow PBR/runtime acceptance.'}


def _linear(path):
    path=Path(path)
    if path.suffix.lower()=='.npy':array=np.load(path,allow_pickle=False)
    else:
        # The existing external-Python EXR backend returns float32 BGR(A),
        # storage rows top first. Preserve HDR values/alpha and reorder only RGB.
        raw=core.read_float_hdr(path)
        if raw.dtype!=np.float32:raise ValueError('Expected float32 linear RGB(A) master')
        array=raw[:,:,[2,1,0,3] if raw.shape[2]==4 else [2,1,0]]
    if array is None or array.ndim!=3 or array.shape[2] not in (3,4) or not np.isfinite(array).all():raise ValueError('Expected finite linear RGB(A) master')
    return np.asarray(array,dtype=np.float32)


def model_contract(model,marker,lightmap_name,tint_name=None):
    nodes=[n for key in ('meshes','transformNodes') for n in model.get(key,[]) if n.get('name')==marker]
    if len(nodes)!=1 or marker!='lightmap_'+Path(lightmap_name).stem:raise ValueError('Exact delivered lightmap filename/marker required')
    metadata=nodes[0].get('metadata',{})
    if metadata.get('kadshowLightmapEncoding')!='rgbd-v1':raise ValueError('Actual model must declare rgbd-v1')
    bound=metadata.get('kadshowLightmapMinDivisor');rgbd_decode(np.ones((1,1,4),np.uint8),bound)
    adjustment=metadata.get('kadshowColourAdjustment')
    if tint_name:
        if (not isinstance(adjustment,dict) or adjustment.get('version')!=1 or adjustment.get('texture')!=tint_name
                or adjustment.get('coordinatesIndex')!=1 or adjustment.get('encoding')!='srgb-linear-multiplier2-alpha'):
            raise ValueError('Actual model tint metadata differs from supplied companion')
        strength=adjustment.get('strength')
        if isinstance(strength,bool) or not isinstance(strength,(int,float)) or not math.isfinite(strength) or not 0<=strength<=1:
            raise ValueError('Actual model tint strength invalid')
    elif adjustment:raise ValueError('Declared model tint requires its actual compressed companion')
    return {'marker':marker,'minimum_divisor':bound,'tint_strength':adjustment['strength'] if tint_name else 0}


def validate_delivery(recipe_path,output,ktx):
    recipe_path=Path(recipe_path).resolve();recipe=read_json(recipe_path);base=recipe_path.parent
    if recipe.get('schema_version')!=1:raise ValueError('Expected decoded-delivery recipe schema 1')
    def path(value):return (base/value).resolve()
    output=Path(output).resolve()
    if output.exists():raise ValueError('Use a new diagnostic output directory')
    ktx=Path(ktx).resolve();source_hashes={str(recipe_path):sha(recipe_path),str(ktx):sha(ktx)}
    def remember(value):
        resolved=path(value);source_hashes[str(resolved)]=sha(resolved);return resolved
    report_path=remember(recipe['conversion_report']);report=read_json(report_path)
    if report.get('status')!='passed':raise ValueError('Retained conversion report must have passed')
    model_path=remember(recipe['model']);model=read_json(model_path)
    delivered_models=[row for row in report.get('delivery_files',[]) if Path(row['path']).name==model_path.name]
    if len(delivered_models)!=1 or delivered_models[0]['sha256']!=sha(model_path):raise ValueError('Actual model differs from completed conversion delivery proof')
    source_blend=remember(recipe['source_blend'])
    if sha(source_blend)!=recipe['source_sha256']:raise ValueError('Declared source blend hash differs')
    lightmap=recipe['lightmap'];tint=recipe.get('tint')
    model_info=model_contract(model,recipe['marker'],Path(lightmap['ktx']).name,Path(tint['ktx']).name if tint else None)
    version=subprocess.run([str(ktx),'--version'],capture_output=True,text=True,check=True,**_process_options()).stdout.strip()
    if '4.4.2' not in version:raise ValueError('Explicit KTX-Software 4.4.2 required')
    output.mkdir(parents=True)
    records=[];bindings={}
    def decode(item,role):
        compressed=remember(item['ktx']);matches=[row for row in report['textures'] if row['output']==compressed.name]
        if len(matches)!=1:raise ValueError('Unique actual conversion texture record required')
        row=matches[0]
        if sha(compressed)!=row['output_sha256']:raise ValueError('Actual compressed delivery hash differs from conversion report')
        prepared_source=Path(row['source_path']).resolve();source_hashes[str(prepared_source)]=sha(prepared_source)
        if sha(prepared_source)!=row['source_sha256']:raise ValueError('Exporter preparation source changed')
        prepared=Path(row['prepared']).resolve();source_hashes[str(prepared)]=sha(prepared)
        if sha(prepared)!=row['prepared_sha256']:raise ValueError('Retained prepared pixels changed')
        origin='bottom-left' if row['flip_y'] else 'top-left'
        if item.get('image_origin')!=origin:raise ValueError('Logical image origin differs from retained exporter flip_y preparation')
        dimensions=item['dimensions'];profile=inspect_ktx2(compressed,role,dimensions,row['mipmaps'],codec=row.get('codec'))
        reference=png_rgba(prepared)
        if reference.shape!=(dimensions[1],dimensions[0],4):raise ValueError('Prepared PNG dimensions differ')
        subprocess.run([str(ktx),'validate',str(compressed)],capture_output=True,text=True,check=True,**_process_options())
        raw=output/(compressed.stem+'.rgba')
        argv=[str(ktx),'extract','--transcode','rgba8','--level','0','--raw',str(compressed),str(raw)]
        subprocess.run(argv,capture_output=True,text=True,check=True,**_process_options())
        if raw.stat().st_size!=dimensions[0]*dimensions[1]*4:raise ValueError('Raw decoded RGBA8 byte count differs')
        actual=np.fromfile(raw,dtype=np.uint8).reshape(dimensions[1],dimensions[0],4)
        reference=top_left(reference,origin);actual=top_left(actual,origin)
        refpath=output/(compressed.stem+'_reference.npy');actualpath=output/(compressed.stem+'_decoded.npy')
        np.save(refpath,reference);np.save(actualpath,actual)
        record={'role':role,'ktx':str(compressed),'sha256':sha(compressed),'prepared':str(prepared),'prepared_sha256':sha(prepared),
                'raw':str(raw),'raw_sha256':sha(raw),'logical_raw_image_origin':origin,'normalized_array_origin':'top-left',
                'profile':profile,'command':argv,'reference_array':str(refpath),'decoded_array':str(actualpath),
                'encoded_rgba_error_vs_prepared':metrics(np.abs(actual.astype(float)-reference.astype(float))/255),
                'orientation_validation':'Declared retained flip_y recipe plus prepared PNG, no flip chosen by visual similarity; KaDshow loader orientation remains untested'}
        records.append(record);return actual,reference,record
    actual,reference,light_record=decode(lightmap,'rgbd')
    if not math.isclose(model_info['minimum_divisor'],float(reference[...,3].min())/255,rel_tol=0,abs_tol=1e-12):
        raise ValueError('Actual model divisor bound differs from retained encoded preparation minimum')
    master_path=remember(lightmap['master']);albedo_path=remember(lightmap['albedo']);mask_path=remember(lightmap['owned_mask'])
    light_source=next(row for row in report['textures'] if row['output']==Path(lightmap['ktx']).name)
    if sha(master_path)!=light_source['source_sha256']:raise ValueError('Comparison master must be exact staged AgX-corrected exporter source')
    master=top_left(_linear(master_path),lightmap['master_origin']);albedo=top_left(_linear(albedo_path),lightmap['albedo_origin'])
    target=lightmap['dimensions'][0]
    if lightmap['dimensions']!=[target,target]:raise ValueError('Square lightmap required')
    master=core.resize_lightmap(master,{'size':target,'color_space':'linear'},True)[...,:3]
    albedo=core.resize_lightmap(albedo,{'size':target,'color_space':'linear'},True)[...,:3]
    labels=top_left(np.load(mask_path,allow_pickle=False),lightmap['mask_origin'])
    if labels.ndim!=2 or not np.isfinite(labels).all() or (labels<0).any():raise ValueError('Expected nonnegative native label/owned mask array')
    ownership_path=remember(lightmap['ownership_receipt']);ownership=read_json(ownership_path)
    if (ownership.get('method')!='native-island-id' or ownership.get('source_sha256')!=recipe['source_sha256']
            or ownership.get('geometry_sha256')!=recipe['geometry_sha256']):raise ValueError('Native source/geometry ownership differs')
    native_path=remember(str(ownership_path.parent/'native_island_ids.npy'));catalogue_path=remember(str(ownership_path.parent/'islands.json'))
    if sha(native_path)!=ownership['native_ids_sha256'] or sha(catalogue_path)!=ownership['catalogue_file_sha256']:
        raise ValueError('Native ownership bytes differ from receipt')
    sys.path.insert(0,str(HERE.parent/'environment_tools'));from native_ownership import validate_native_ids
    native=np.load(native_path,allow_pickle=False);raw=[]
    for mode in ('direct','indirect'):
        raw_path=remember(str(ownership_path.parent/(mode+'_raw.npy')))
        if sha(raw_path)!=ownership['raw_sha256'][mode]:raise ValueError('Native transport bytes changed')
        raw.append(np.load(raw_path,allow_pickle=False))
    native_labels,native_report=validate_native_ids(native,read_json(catalogue_path),*raw)
    # Blender/native arrays are top-left here; the explicit mask origin must reproduce these exact labels.
    if not np.array_equal(labels,native_labels):raise ValueError('Supplied comparison labels differ from actual native IDs')
    mask=np.repeat((labels>0)[...,None],4,axis=2).astype(np.float32)
    owned=core.resize_lightmap(mask,{'size':target,'color_space':'linear'},True)[...,0]>0
    light_record['comparison']=compare_rgbd(actual,reference,master,owned,albedo,model_info['minimum_divisor'])
    light_record['owned_mask_rule']='Export texel is occupied when any area-averaged native coverage contributes; exact supplied native mask, no inferred owner'
    bindings['lightmap']=light_record
    if tint:
        actual,reference,tint_record=decode(tint,'tint');snapshot=remember(tint['uv_snapshot']);expected=remember(tint['expected'])
        settings=dict(tint.get('sampling',{}),strength=model_info['tint_strength'],image_origin='top-left',assume_srgb=True,alpha_mode='straight')
        tint_record['decoded_filter_diagnostic']=diagnose(read_json(snapshot),actual,read_json(expected),**settings)
        tint_record['reference_filter_diagnostic']=diagnose(read_json(snapshot),reference,read_json(expected),**settings)
        bindings['tint']=tint_record
    atlas_records=[]
    for item in recipe.get('colour_atlases',[]):
        actual,reference,atlas_record=decode(item,'colour-atlas');atlas_record['image_name']=item['image_name'];atlas_records.append(atlas_record)
    bindings['colour_atlases']=atlas_records
    if len({record['image_name'] for record in atlas_records})!=len(atlas_records):raise ValueError('Duplicate colour-atlas source image binding')
    references={owner[key] for owner,key,_,_ in core.texture_fields(model)}
    if any(Path(row['ktx']).name not in references for row in atlas_records):raise ValueError('Colour-atlas companion not referenced by actual model')
    scalar_records=[]
    for item in recipe.get('scalar_materials',[]):
        actual,reference,scalar_record=decode(item,'material-linear')
        if Path(scalar_record['ktx']).name not in references:raise ValueError('Linear material companion not referenced by actual model')
        scalar_record['semantic']=item['semantic'];scalar_record['limits']='Encoded linear channel numeric error only, not material/PBR appearance validation'
        scalar_records.append(scalar_record)
    bindings['scalar_materials']=scalar_records
    equivalence=None
    if recipe.get('geometry_equivalence_receipt'):
        equivalence_path=remember(recipe['geometry_equivalence_receipt']);equivalence=read_json(equivalence_path)
        if (not equivalence.get('geometry_and_UV1_untouched') or equivalence.get('ambiguous_shared_corner_assignments')!=0
                or equivalence.get('exact_float32_UV2_corner_mismatches')!=[] or not equivalence.get('matched_corner_count',0)>0):
            raise ValueError('Connected/runtime floor requires existing exact UV/geometry parity evidence')
    mesh_names=recipe.get('runtime_mesh_names')
    indexed=[node for node in model.get('meshes',[]) if node.get('indices')]
    if mesh_names is not None:
        if not isinstance(mesh_names,list) or not mesh_names or any(not isinstance(name,str) or not name for name in mesh_names) or len(set(mesh_names))!=len(mesh_names):raise ValueError('Explicit runtime mesh names must be nonempty and unique')
        runtime=[node for node in indexed if node.get('name') in mesh_names]
        if len(runtime)!=len(mesh_names):raise ValueError('Runtime geometry selection differs from actual exported names')
    else:
        runtime=[node for node in indexed if node.get('isVisible',True) and node.get('isEnabled',True) and node.get('visibility',1)>0]
    if not runtime or any(len(node['indices'])%3 for node in runtime):raise ValueError('Expected actual indexed triangle runtime geometry')
    continuous_surface=None
    if recipe.get('continuous_surface_equivalence'):
        from surface_equivalence import validate_surface_evidence
        proof_path=remember(recipe['continuous_surface_equivalence']);proof=read_json(proof_path)
        checked=validate_surface_evidence(proof,source_sha256=recipe['source_sha256'],native_geometry_sha256=recipe['geometry_sha256'])
        if (set(proof['runtime_mesh_names'])!={node['name'] for node in runtime}
                or checked['runtime_triangle_count']!=sum(len(node['indices'])//3 for node in runtime)):
            raise ValueError('Exported runtime mesh/count differs from continuous surface proof')
        continuous_surface={'proof':proof,'validation':checked,'file':str(proof_path),'sha256':sha(proof_path)}
    generated={str(Path(row[key])):sha(row[key]) for row in records for key in ('reference_array','decoded_array','raw')}
    for tool in (Path(__file__),HERE/'uv_tint_filter.py',HERE.parent/'ktx_conversion.py',HERE.parent/'environment_tools/native_ownership.py'):
        source_hashes[str(tool.resolve())]=sha(tool)
    contract={'schema_version':1,'kind':'decoded-delivery-diffuse-contract','source_sha256':recipe['source_sha256'],
        'geometry_sha256':recipe['geometry_sha256'],'model':str(model_path),'model_sha256':sha(model_path),'model_contract':model_info,
        'runtime_mesh_count':len(runtime),'runtime_triangle_count':sum(len(node['indices'])//3 for node in runtime),
        'runtime_mesh_names':[node['name'] for node in runtime],'excluded_indexed_meshes':[node['name'] for node in indexed if node not in runtime],
        'runtime_selection':'Explicit project runtime_mesh_names' if mesh_names is not None else 'Model indexed meshes with own isVisible/isEnabled and positive visibility; no application culling equivalence claim',
        'geometry_equivalence_receipt':equivalence,'continuous_surface_equivalence':continuous_surface,'native_ownership':native_report,
        'inputs':source_hashes,'generated':generated,'bindings':bindings,'ktx_version':version,
        'display':{'shader_gamma':1/2.2,'view_transform':'Raw','exposure':0,'gamma':1,'look':'None'},
        'limits':'Local diffuse reconstruction, not KaDshow runtime/PBR validation; original glass/background shown unchanged under Raw outside codec objective. Finite UV tint probes and camera views cannot establish exhaustive seam/coverage acceptance.'}
    if continuous_surface:
        for tool in (HERE/'surface_equivalence.py',HERE/'uv_audit.py'):source_hashes[str(tool.resolve())]=sha(tool)
    for filename,digest in source_hashes.items():
        if sha(filename)!=digest:raise ValueError('Input changed during decode: '+filename)
    save(output/'delivery-contract.json',contract)
    return contract


def load_contract(path):
    record=read_json(path)
    if record.get('schema_version')!=1 or record.get('kind')!='decoded-delivery-diffuse-contract':raise ValueError('Expected decoded-delivery contract')
    for filename,digest in {**record['inputs'],**record['generated']}.items():
        if sha(filename)!=digest:raise ValueError('Decoded delivery input/generated bytes changed: '+filename)
    continuous=record.get('continuous_surface_equivalence')
    if continuous:
        from surface_equivalence import validate_surface_evidence
        checked=validate_surface_evidence(continuous['proof'],source_sha256=record['source_sha256'],native_geometry_sha256=record['geometry_sha256'])
        if checked!=continuous['validation']:raise ValueError('Continuous surface contract validation differs')
    lightmap=record['bindings']['lightmap'];tint=record['bindings'].get('tint')
    actual=model_contract(read_json(record['model']),record['model_contract']['marker'],Path(lightmap['ktx']).name,
                          Path(tint['ktx']).name if tint else None)
    if actual!=record['model_contract']:raise ValueError('Decoded delivery contract differs from actual model metadata')
    return record


def _process_options():return {'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}


def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    validate=sub.add_parser('validate');validate.add_argument('--recipe',required=True,type=Path);validate.add_argument('--output',required=True,type=Path);validate.add_argument('--ktx',required=True,type=Path)
    views=sub.add_parser('render-views');views.add_argument('--config',required=True,type=Path);views.add_argument('--candidate',required=True,type=Path);views.add_argument('--output',required=True,type=Path);views.add_argument('--blender',required=True,type=Path)
    args=parser.parse_args()
    if args.command=='validate':validate_delivery(args.recipe,args.output,args.ktx)
    else:
        output=args.output.resolve()
        if output.exists():raise ValueError('Use a new delivery view output directory')
        output.mkdir(parents=True)
        argv=[str(args.blender),'--background','--factory-startup']
        sys.path.insert(0,str(HERE.parent/'environment_tools'));from common import load_config
        config=load_config(args.config)
        if not config['capture']['views'].get('delivery_contract'):raise ValueError('Delivery views require capture.views.delivery_contract')
        recipe_inputs={str(args.config.resolve()):sha(args.config),config['_source']:sha(config['_source'])}
        for file in (HERE.parent/'environment_tools').glob('*.py'):recipe_inputs[str(file.resolve())]=sha(file)
        for relative in config.get('source_images',[]):
            file=(Path(config['_root'])/relative).resolve();recipe_inputs[str(file)]=sha(file)
        argv += [config['_source'],'--python-exit-code','1','--python',str(HERE.parent/'environment_tools/worker.py'),'--',str(args.config.resolve()),'delivery-views',str(args.candidate.resolve()),str(output)]
        with (output/'blender.log').open('w',encoding='utf8') as stream:subprocess.run(argv,stdout=stream,stderr=subprocess.STDOUT,check=True,**_process_options())
        result=read_json(output/'result.json');pairs={}
        for view in result['views']:
            key=json.dumps({field:view[field] for field in ('camera','matrix_world','camera_properties')},sort_keys=True)
            pairs.setdefault(key,{})[view['mode']]=view
        from PIL import Image
        comparisons=[]
        for pair in pairs.values():
            reference=pair['delivery-reference'];decoded=pair['delivery-decoded']
            with Image.open(output/reference['file']) as image:first=np.asarray(image).astype(float)/255
            with Image.open(output/decoded['file']) as image:second=np.asarray(image).astype(float)/255
            if first.shape!=second.shape:raise ValueError('Matched delivery PNG dimensions differ')
            comparisons.append({'reference':reference['file'],'decoded':decoded['file'],'stored_png_error':metrics(np.abs(first-second)),
                'limits':'Full-frame stored PNG difference includes identical physical Raw glass/background and render noise; occupied linear/shaded texel errors are in delivery contract. Multi-view seams/detail/neutral regions still require inspection.'})
        save(output/'matched-codec-comparison.json',{'schema_version':1,'views':comparisons,'accepted':False})
        for filename,digest in recipe_inputs.items():
            if sha(filename)!=digest:raise ValueError('Delivery view source/config/tool changed: '+filename)
        save(output/'render-inputs.json',{'schema_version':1,'inputs':recipe_inputs,'command':argv,'source_saved':False,
            'limits':'Direct disposable worker views only; no bake chain, no KaDshow runtime test'})


if __name__=='__main__':main()
