"""NativeRGBA8 volume audit; frozen screens remain distinct from current shader-order math."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import numpy as np

HERE=Path(__file__).resolve().parent
if str(HERE) not in sys.path:sys.path.insert(0,str(HERE))
import decoded_delivery as dd

def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def save(path,value):Path(path).write_text(json.dumps(value,indent=2,allow_nan=False)+'\n',encoding='utf8')
def require(test,message):
    if not test:raise ValueError(message)
def norm_error(actual,reference):return np.max(np.abs(actual-reference),axis=-1)/np.maximum(1,np.max(reference,axis=-1))

def fields(atlas,banks):
    require(atlas.shape[:2]==(128*banks,128),'Expected unchanged32x16x32 four-column volume atlas')
    output=np.empty((banks,32,16,32,atlas.shape[-1]),dtype=atlas.dtype)
    for bank in range(banks):
        for y in range(16):
            row,column=divmod(y,4)
            output[bank,:,y]=atlas[bank*128+row*32:bank*128+(row+1)*32,column*32:(column+1)*32]
    return output

def trilinear(field,points):
    """Texel-centered, clamp-to-edge sampling; outside the outer edges is neutral."""
    require(field.ndim==4 and points.ndim==2 and points.shape[1]==3 and np.isfinite(points).all(),'Expected finiteN3lookup coordinates')
    size=np.array([field.shape[2],field.shape[1],field.shape[0]])
    p=np.clip(points*size-.5,0,size-1);low=np.floor(p).astype(int);high=np.minimum(low+1,size-1);fraction=p-low
    result=np.zeros((len(points),field.shape[-1]),dtype=np.float64)
    for z in (0,1):
        for y in (0,1):
            for x in (0,1):
                ix=high[:,0]if x else low[:,0];iy=high[:,1]if y else low[:,1];iz=high[:,2]if z else low[:,2]
                weight=(fraction[:,0]if x else 1-fraction[:,0])*(fraction[:,1]if y else 1-fraction[:,1])*(fraction[:,2]if z else 1-fraction[:,2])
                result+=field[iz,iy,ix]*weight[:,None]
    result[np.any((points<0)|(points>1),axis=1)]=1
    return result

def encoded_first(field,points,floor):
    values=trilinear(field,points)
    return values[:,:3]**2.2/np.maximum(values[:,3:4],floor)

def preview(path,reference,prepared,decoded,error):
    from PIL import Image
    grayscale=np.repeat(np.clip(error[...,None]/.1,0,1),3,axis=-1)
    panel=np.concatenate((reference,prepared,decoded,grayscale),axis=1)
    panel=np.maximum(panel,0);panel=panel/(1+panel)
    display=np.where(panel<=.0031308,12.92*panel,1.055*np.power(panel,1/2.4)-.055)
    Image.fromarray(np.rint(np.clip(display,0,1)*255).astype(np.uint8),'RGB').save(path)

def audit(recipe_path,output,ktx):
    recipe_path=Path(recipe_path).resolve();recipe=read(recipe_path)
    output=Path(output).resolve();require(not output.exists(),'Use a fresh volume audit output')
    screens=read(recipe['screens']);source=Path(recipe['source']['path']);source_hash=recipe['source']['sha256']
    require(dd.sha(source)==source_hash==screens['source_sha256'],'Volume audit source changed')
    receipt_path=Path(recipe['receipt']);require(dd.sha(receipt_path)==screens['receipt_sha256'],'Volume audit receipt changed')
    receipt=read(receipt_path);report=read(recipe['assembly_report']);model_path=Path(recipe['model'])
    require(report.get('kind')=='separate-source-volume-assembly-v1' and report.get('source_sha256')==source_hash,'Expected separate-source assembly report')
    require(dd.sha(model_path)==report['model_sha256'],'Assembled model changed')
    for relative,digest in report['final_files'].items():require(dd.sha(model_path.parent/relative)==digest,'Assembled package file changed: '+relative)
    model=read(model_path);markers=[n for n in model.get('meshes',[])if n.get('name','').lower()=='hasenv']
    require(len(markers)==1 and not markers[0].get('positions') and not markers[0].get('indices'),'Expected one empty hasenv mesh')
    descriptor=markers[0]['metadata']['kadshowEnvironmentLightingVolume']
    sites=Path(recipe['lookup_sites']);index=Path(recipe['lookup_index'])
    require(dd.sha(sites)==screens['quality']['lookup_sites_sha256'] and dd.sha(index)==screens['quality']['lookup_index_sha256'],'Frozen lookup coordinates changed')
    points=np.load(sites,allow_pickle=False);require(points.dtype==np.float64 and list(points.shape)==read(index)['shape'],'Frozen lookup shape differs')
    output.mkdir(parents=True);assets=[]
    try:
        for role,banks,conversion,metadata,master_name in (
            ('Simple',1,report['stage_b']['report'],descriptor,receipt['master']),
            ('Advanced',6,report['stage_b']['report']['advanced'],descriptor['advanced'],receipt['advanced']['master'])):
            asset=screens['assets'][role];master_path=receipt_path.parent/master_name
            require(dd.sha(master_path)==asset['master_sha256'],'Unmodified volume master changed: '+role)
            import OpenImageIO as oiio
            master=oiio.ImageBuf(str(master_path)).get_pixels(oiio.FLOAT)
            require(master is not None and np.isfinite(master).all() and np.all(master[...,:3]>=0),'Invalid linear volume master')
            master=master[...,:3].astype(np.float64)
            prepared_path=Path(conversion['metrics']['prepared']);prepared=dd.png_rgba(prepared_path)
            expected,_=dd.core.encode_rgbd(master.astype(np.float32),asset['divisor_mode']if role=='Simple'else'per-pixel',fixed_divisor=role=='Advanced')
            require(np.array_equal(prepared,expected),'Actual PNG differs from whole original float-master encoding')
            guard=conversion['metrics'].get('source_preparation_guard',{})
            require(guard.get('when')=='before-ktx-create' and guard.get('byte_exact')is True and
                guard.get('prepared_sha256')==dd.sha(prepared_path) and guard.get('source_sha256')==dd.sha(master_path) and
                guard.get('rgba_sha256')==hashlib.sha256(prepared.tobytes()).hexdigest(),'Missing actual producer pre-encoding byte guard')
            compressed=model_path.parent/conversion['texture'];require(dd.sha(compressed)==conversion['textureSha256'],'Final volume KTX bytes changed')
            profile=dd.inspect_ktx2(compressed,'rgbd',asset['dimensions'],False,codec='uastc')
            subprocess.run([str(ktx),'validate',str(compressed)],capture_output=True,text=True,check=True,**dd._process_options())
            raw=output/(role+'.rgba');argv=[str(ktx),'extract','--transcode','rgba8','--level','0','--raw',str(compressed),str(raw)]
            subprocess.run(argv,capture_output=True,text=True,check=True,**dd._process_options())
            width,height=asset['dimensions'];require(raw.stat().st_size==width*height*4,'NativeRGBA8byte count differs')
            decoded=np.fromfile(raw,dtype=np.uint8).reshape(height,width,4)
            D=int(prepared[...,3].min());floor=D/255
            require(metadata.get('minDivisor')==floor and metadata.get('strength')==1 and not isinstance(metadata.get('strength'),bool),'Original minimum divisor/strength metadata differs')
            if role=='Advanced':
                proof=conversion.get('advancedAlphaPolicy',{})
                require(np.all(prepared[...,3]==D) and np.all(decoded[...,3]<=D) and np.all(np.maximum(decoded[...,3],D)==D),'Advanced original divisor ceiling failed')
                require(proof.get('RGB_byte_exact')is True and proof.get('RGB_before_sha256')==proof.get('RGB_after_sha256') and
                    dd.sha(proof['after_raw'])==proof['after_raw_sha256']==dd.sha(raw) and dd.sha(proof['before_raw'])==proof['before_raw_sha256'],
                    'Advanced actual nativeRGBA8RGB-invariance proof differs')
                before=np.fromfile(proof['before_raw'],dtype=np.uint8).reshape(height,width,4)
                require(np.array_equal(before[...,:3],decoded[...,:3]) and hashlib.sha256(before[...,:3].tobytes()).hexdigest()==proof['RGB_before_sha256'],'Advanced decoded RGB bytes changed during alpha enforcement')
            actual=dd.rgbd_decode(decoded,floor);prepared_linear=dd.rgbd_decode(prepared,floor)
            comparison=dd.compare_rgbd(decoded,prepared,master,np.ones(master.shape[:2],dtype=bool),np.ones_like(master),floor)
            total=norm_error(actual,master);checks={'maximum_normalized_per_texel_RGB_error':float(total.max())<=screens['quality']['maximum_normalized_per_texel_RGB_error']}
            mf=fields(master,banks);df=fields(actual,banks);pf=fields(prepared_linear,banks)
            encoded=fields(decoded.astype(np.float64)/255,banks);prepared_encoded=fields(prepared.astype(np.float64)/255,banks)
            bank_reports=[]
            for bank in range(banks):
                sl=slice(bank*128,(bank+1)*128);m=master[sl];a=actual[sl]
                relative=np.abs(a.mean((0,1))-m.mean((0,1)))/np.maximum(1e-6,np.abs(m.mean((0,1))))
                checks['bank%d_channel_mean_relative_error'%bank]=bool(np.all(relative<=screens['quality']['per_bank_each_channel_relative_mean_error']))
                reference=trilinear(mf[bank],points);voxel=trilinear(df[bank],points)
                primary=norm_error(voxel,reference)
                checks['bank%d_frozen_decode_voxel_first_lookup'%bank]=float(primary.max())<=screens['quality']['maximum_primary_lookup_normalized_RGB_error']
                current=encoded_first(encoded[bank],points,floor);current_prepared=encoded_first(prepared_encoded[bank],points,floor)
                image=output/('%s_bank%d.png'%(role,bank));preview(image,m,prepared_linear[sl],a,total[sl])
                bank_reports.append(dict(bank=bank,channel_mean_relative_error=relative.tolist(),normalized_texel_error=dd.metrics(total[sl]),
                    chromaticity_error=dd.metrics(np.abs(a/np.maximum(a.sum(-1,keepdims=True),1e-9)-m/np.maximum(m.sum(-1,keepdims=True),1e-9))),
                    frozen_primary_lookup=dict(order='decode every voxel then trilinear; historical frozen-screen assumption, not current consumer math',error=dd.metrics(primary)),
                    current_client_math=dict(order='3D encodedRGBAfilter then gamma2.2/floored divisor; mathematical shader order, hardware/runtime acceptance pending',
                        total_error=dd.metrics(norm_error(current,reference)),codec_vs_prepared=dd.metrics(np.abs(current-current_prepared))),
                    preview=str(image),preview_sha256=dd.sha(image)))
            np.savez_compressed(output/(role+'_arrays.npz'),reference=master,prepared=prepared,decoded=decoded,reconstructed=actual,normalized_error=total)
            assets.append(dict(mode=role,quality_pass=all(checks.values()),checks=checks,failed_screens=[k for k,v in checks.items()if not v],
                comparison=comparison,encoded_rgba_error=dd.metrics(np.abs(decoded.astype(float)-prepared.astype(float))/255),
                normalized_total_error=dd.metrics(total),profile=profile,decode_command=argv,raw_sha256=dd.sha(raw),
                original_prepared_divisor_byte=D,banks=bank_reports,unit_albedo_diagnostic_only=True))
        require(dd.sha(source)==source_hash,'Saved source changed during audit')
        result=dict(kind='nativeRGBA8-volume-audit-v1',execution_completed=True,quality_pass=all(a['quality_pass']for a in assets),
            source_sha256=source_hash,receipt_sha256=dd.sha(receipt_path),model_sha256=dd.sha(model_path),screens_sha256=dd.sha(recipe['screens']),
            assets=assets,previous_exceptions_inherited=False,codec_exception_approved=False,
            limits='Completed quality failures retain exit0 and remain failures; structural/source/alpha failures raise. Both lookup orders are offline math; target hardware/PBR acceptance remains separate.')
        save(output/'volume-audit.json',result);return result
    except Exception as error:
        save(output/'HARD_FAILURE.json',dict(type=type(error).__name__,message=str(error),completed_assets=assets));raise
