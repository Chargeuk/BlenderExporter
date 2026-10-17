"""Opt-in measured local island repair; immutable baseline, real current full IDs."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile
import numpy as np

# Blender --python executes an absolute script without adding its directory.
# Bootstrap the same sibling modules used by external Python before importing them.
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
from common import load_config,save_json,sha
from native_ownership import catalogue_identity,validate_native_ids

METHOD='native-local-island-patch'
ARRAYS=('bake/direct_raw.npy','bake/indirect_raw.npy','ownership/direct_raw.npy','ownership/indirect_raw.npy',
        'process/direct/interiors.npy','process/indirect/interiors.npy','response-capture/source_base_colour.npy',
        'response-capture/diffuse_colour.npy','response/factor.npy','response/diffuse_colour_owned.npy',
        'response/direct_response_raw_owned.npy','response/indirect_response_raw_owned.npy',
        'response/direct_response_processed_owned.npy','response/indirect_response_processed_owned.npy','ao/ao_raw.npy')
MASTERS={'direct_final.exr':'process/direct/interiors.npy','indirect_final.exr':'process/indirect/interiors.npy',
         'direct_response_raw.exr':'response/direct_response_raw_owned.npy','indirect_response_raw.exr':'response/indirect_response_raw_owned.npy',
         'direct_response_processed.exr':'response/direct_response_processed_owned.npy','indirect_response_processed.exr':'response/indirect_response_processed_owned.npy',
         'ao.exr':'ao/ao_raw.npy','albedo_uv.exr':'albedo_owned.npy'}


def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def chart_key(row):return row['object'],row['polygon_root']
def uv_key(triangle):return np.asarray(triangle,dtype='<f4').tobytes()


def verify_tool_provenance(tool_root,required,provenance_path):
    """Patch-only retained source authority: pinned ZIP, whole runtime source, stage tools."""
    root=Path(tool_root).resolve();path=Path(provenance_path).resolve();record=read(path)
    if not {'src/babylon_js/environment_tools/worker.py','src/babylon_js/environment_tools/native_ownership.py'}<=set(required):
        raise ValueError('Local native stage requires actual worker/native ownership tool hashes')
    if record.get('kind')!='v013-functional-source-baseline' or Path(record['baseline_root']).resolve()!=root:
        raise ValueError('Patch local tool provenance must describe the supplied retained tool root')
    package=Path(record['baseline_zip']);package_hash=record['baseline_zip_sha256']
    if sha(package)!=package_hash:raise ValueError('Retained local package changed')
    entries={row['relative_path']:row for row in record['baseline_files']}
    checked={str(path):sha(path),str(package):package_hash};count=0
    with zipfile.ZipFile(package) as archive:
        for member in archive.namelist():
            if member.endswith('/'):continue
            relative='src/'+member;blob=archive.read(member);expected=hashlib.sha256(blob).hexdigest()
            if relative not in entries or entries[relative]['sha256']!=expected or sha(root/relative)!=expected:
                raise ValueError('Retained local package/source inventory differs: '+relative)
            checked[str((root/relative).resolve())]=expected;count+=1
    if count!=record['verified_zip_members']:raise ValueError('Retained local runtime package is incomplete')
    for relative,expected in required.items():
        source=(root/relative).resolve()
        if (not source.is_relative_to(root) or relative not in entries or entries[relative]['sha256']!=expected or sha(source)!=expected):
            raise ValueError('Actual local stage tool bytes differ: '+relative)
        checked[str(source)]=expected
    return checked


def catalogue_members(snapshot,catalogue):
    """Use existing evaluated snapshot; exact UV triangle bytes disambiguate catalogue members."""
    catalogue_identity(catalogue);by_uv={};members={};used=set()
    for triangle in snapshot['uv']['triangles']:
        by_uv.setdefault((triangle['object'],uv_key(triangle['uv'])),[]).append(triangle)
    for chart in catalogue:
        rows=[]
        for uv in chart['triangles']:
            matches=by_uv.get((chart['object'],uv_key(uv)),[])
            if len(matches)!=1:raise ValueError('Catalogue triangle membership is missing/ambiguous')
            row=matches[0];identity=(row['object'],row['triangle'])
            if identity in used:raise ValueError('Catalogue triangle appears in multiple islands')
            used.add(identity);rows.append(row)
        key=chart_key(chart)
        if key in members:raise ValueError('Duplicate chart object/root identity')
        members[key]=rows
    return members


def validate_layout(before,current,old_catalogue,current_catalogue,local_catalogue,affected):
    if not isinstance(affected,list) or not 1<=len(affected)<=128:raise ValueError('Explicit bounded affected chart membership required')
    if before['invariants']!=current['invariants']:raise ValueError('Physical geometry/UV1/material/World/control/source-image state changed')
    old_rows={(row['object'],row['triangle']):row for row in before['uv']['triangles']}
    new_rows={(row['object'],row['triangle']):row for row in current['uv']['triangles']}
    if set(old_rows)!=set(new_rows):raise ValueError('Evaluated geometry triangle membership changed')
    old_members=catalogue_members(before,old_catalogue);new_members=catalogue_members(current,current_catalogue)
    local_members=catalogue_members(current,local_catalogue)
    if set(old_members)!=set(new_members):raise ValueError('Full current chart structure/object roots changed')
    allowed=set();keys=set();objects=set()
    for chart in affected:
        key=chart_key(chart)
        if key in keys or key not in old_members or key not in new_members:raise ValueError('Affected chart root is duplicate/unknown')
        keys.add(key);objects.add(chart['object'])
        triangles=set(chart['triangles']);polygons=set(chart['polygons'])
        if not triangles or len(triangles)!=len(chart['triangles']) or len(polygons)!=len(chart['polygons']):raise ValueError('Explicit exact chart members required')
        for members in (old_members[key],new_members[key],local_members.get(key,[])):
            if {row['triangle'] for row in members}!=triangles or {row['polygon'] for row in members}!=polygons:
                raise ValueError('Affected whole-chart polygon/triangle membership differs')
        allowed.update((chart['object'],index) for index in triangles)
    if {key[0] for key in local_members}!=objects:raise ValueError('Local candidate must contain exactly affected receiver objects')
    for key,members in local_members.items():
        if key not in new_members or members!=new_members[key]:raise ValueError('Local chart geometry/UV differs from full current catalogue')
    changed=set()
    for identity,old in old_rows.items():
        new=new_rows[identity]
        if {k:v for k,v in old.items() if k not in ('uv','chart')}!={k:v for k,v in new.items() if k not in ('uv','chart')}:
            raise ValueError('Geometry/polygon/loops/vertices changed')
        if uv_key(old['uv'])!=uv_key(new['uv']):changed.add(identity)
    if not changed or not changed<=allowed or {name for name,index in changed}!=objects:
        raise ValueError('UV changes must be confined to every explicitly authorized whole chart')
    old_by_key={chart_key(row):row for row in old_catalogue};new_by_key={chart_key(row):row for row in current_catalogue}
    local_by_key={chart_key(row):row for row in local_catalogue}
    return {'old_to_current':{str(row['id']):new_by_key[key]['id'] for key,row in old_by_key.items()},
        'local_to_current':{str(row['id']):new_by_key[key]['id'] for key,row in local_by_key.items()},
        'old_affected_ids':[old_by_key[key]['id'] for key in sorted(keys)],
        'current_affected_ids':[new_by_key[key]['id'] for key in sorted(keys)],
        'local_affected_ids':[local_by_key[key]['id'] for key in sorted(keys)],
        'affected_objects':sorted(objects),'changed_triangles':len(changed),'authorized_triangles':len(allowed)}


def patch_masks(old_labels,current_labels,local_labels,mapping):
    if old_labels.shape!=current_labels.shape or local_labels.shape!=current_labels.shape:raise ValueError('Ownership dimensions differ')
    old_affected=np.isin(old_labels,mapping['old_affected_ids']);new_affected=np.isin(current_labels,mapping['current_affected_ids'])
    local_affected=np.isin(local_labels,mapping['local_affected_ids'])
    mapped_old=np.zeros(old_labels.shape,np.int32);mapped_local=np.zeros(local_labels.shape,np.int32)
    for old,new in mapping['old_to_current'].items():mapped_old[old_labels==int(old)]=new
    for old,new in mapping['local_to_current'].items():mapped_local[local_labels==int(old)]=new
    unrelated=(current_labels>0)&~new_affected
    if not np.array_equal((old_labels>0)&~old_affected,unrelated) or not np.array_equal(mapped_old[unrelated],current_labels[unrelated]):
        raise ValueError('Actual unrelated current ownership changed; no conservative owner substitution')
    if not np.array_equal(new_affected,local_affected) or not np.array_equal(mapped_local[new_affected],current_labels[new_affected]):
        raise ValueError('Actual affected full/current versus local pixel ownership differs')
    if np.any((old_affected|new_affected)&unrelated):raise ValueError('Patch union intersects unrelated owned pixels')
    if not new_affected.any():raise ValueError('Affected current charts have no actual native samples')
    return old_affected|new_affected,new_affected,unrelated


def front_coverage(snapshot,catalogue,labels,affected,minimum_rows=6):
    """Classify actual owned centres strictly inside target face, never infer an owner."""
    by_key={chart_key(row):row for row in catalogue};members=catalogue_members(snapshot,catalogue);records=[]
    for chart in affected:
        key=chart_key(chart);target=chart.get('primary_polygon',4)
        triangles=[row['uv'] for row in members[key] if row['polygon']==target]
        if not triangles:raise ValueError('Affected chart lacks explicit broad target polygon')
        y,x=np.where(labels==by_key[key]['id']);points=np.column_stack(((x+.5)/labels.shape[1],1-(y+.5)/labels.shape[0]));inside=np.zeros(len(x),bool)
        for triangle in triangles:
            triangle=np.asarray(triangle,dtype=np.float64);edges=np.roll(triangle,-1,axis=0)-triangle
            relative=points[:,None,:]-triangle[None,:,:]
            cross=edges[None,:,0]*relative[:,:,1]-edges[None,:,1]*relative[:,:,0]
            inside|=(cross>0).all(axis=1)|(cross<0).all(axis=1)
        rows=np.unique(y[inside]);columns=np.unique(x[inside])
        if len(rows)<minimum_rows:raise ValueError('Broad target polygon actual owned raster rows below required minimum')
        records.append({'object':chart['object'],'polygon_root':chart['polygon_root'],'primary_polygon':target,
            'owned_face_pixels':int(inside.sum()),'owned_rows':len(rows),'owned_columns':len(columns),'minimum_rows':minimum_rows,
            'limits':'Actual native-ID-owned texel centres strictly inside exact evaluated target UV triangles; boundary centres excluded. Whole-chart bevel bbox is not face coverage; no inferred ownership/continuous coverage claim.'})
    return records


def assemble_array(baseline,local,old_labels,current_labels,local_labels,mapping):
    if (baseline.dtype!=np.float32 or local.dtype!=np.float32 or baseline.shape!=local.shape
            or baseline.shape!=old_labels.shape+(4,) or not np.isfinite(baseline).all() or not np.isfinite(local).all()):
        raise ValueError('Patch fields require matching finite float32 RGBA arrays')
    clear,new,unrelated=patch_masks(old_labels,current_labels,local_labels,mapping)
    result=baseline.copy();result[clear]=0;result[new]=local[new]
    if not np.array_equal(result[unrelated].view(np.uint32),baseline[unrelated].view(np.uint32)):
        raise ValueError('Unrelated owned pixels changed bitwise')
    return result


def validate_configs(baseline,current,local,affected_objects):
    def normalized(c,local_scope=False):
        result={key:copy.deepcopy(value) for key,value in c.items() if not key.startswith('_')}
        result.pop('blend',None)
        # These describe the source lineage and future derived output, not transport.
        # Their factual identities are checked independently below against disk/capture.
        result.pop('project_provenance',None)
        result.pop('combined_provenance',None)
        if local_scope:
            for name in ('receivers','contributors','metal_receivers'):result.pop(name,None)
        result.get('physical_colour_adjustment',{}).pop('geometry_sha256',None)
        return result
    if normalized(baseline)!=normalized(current):raise ValueError('Full current recipe changed outside source/UV geometry guard')
    if normalized(baseline,True)!=normalized(local,True):raise ValueError('Local physical/world/tint/control/response/AO recipe changed')
    full=set(current['receivers']['names']);receivers=set(local['receivers']['names']);contributors=set(local['contributors']['names'])
    if set(current.get('contributors',{}).get('names',[])) or receivers!=set(affected_objects) or receivers&contributors or receivers|contributors!=full:
        raise ValueError('Local real receiver/contributor partition differs from immutable full transport geometry')
    if set(local.get('metal_receivers',{}).get('names',[]))!=set(current.get('metal_receivers',{}).get('names',[]))&receivers:
        raise ValueError('Local metallic cap receiver subset differs')
    for c in (baseline,current,local):
        if c.get('samples')!={'DIRECT':128,'INDIRECT':2048} or c.get('quality')!='final' or c.get('ao',{}).get('samples')!=128:
            raise ValueError('Measured patch requires fresh final D128/I2048/AO128')
        if not c.get('receiver_response',{}).get('enabled') or c.get('finishing_overrides'):
            raise ValueError('Fresh measured receiver response and unchanged saved finishing required')


def validate_project_metadata(c,geometry,baseline_source=None,baseline_candidate=None):
    """Check provenance facts separately; descriptive/output lineage is not physics."""
    identity=geometry['geometry_sha256']
    if c.get('physical_colour_adjustment',{}).get('geometry_sha256')!=identity:
        raise ValueError('Physical recipe geometry differs from actual evaluated geometry')
    project=c.get('project_provenance')
    if not isinstance(project,dict):raise ValueError('Truthful project provenance is required')
    expected={'source_sha256':sha(c['_source']),'geometry_sha256':identity,
        'source_image_sha256':{relative:sha(Path(c['_root'])/relative) for relative in c['source_images']},
        'physical_preview_export_strength':c['physical_colour_adjustment']['strength'],
        'immutable_decode_source_required':Path(c['_source']).name,
        'fixed_sampling':True,'adaptive_sampling':False,'cycles_denoising':False,
        'analytic_lights_excluded':True,'authoritative_source_immutable':True}
    if any(project.get(key)!=value for key,value in expected.items()):
        raise ValueError('Project source/images/geometry/role/strength metadata differs from actual inputs')
    roles=project.get('role_partition',{})
    if (roles.get('receivers')!=len(c['receivers']['names'])
            or roles.get('additional_contributors')!=len(c.get('contributors',{}).get('names',[]))
            or roles.get('corrected_role_geometry_sha256')!=identity):
        raise ValueError('Project role partition differs from actual geometry/scope')
    lineage=project.get('local_patch_lineage')
    if baseline_source is not None and (not isinstance(lineage,dict) or lineage.get('baseline_source_sha256')!=baseline_source
            or Path(lineage.get('baseline_candidate','')).name!=Path(baseline_candidate).name):
        raise ValueError('Project patch lineage differs from immutable baseline')


def _snapshot_check(path,c,tool_root=None):
    path=Path(path).resolve();receipt=read(path.parent/'receipt.json');snapshot=read(path)
    if (receipt['source_sha256']!=sha(c['_source']) or receipt['config_sha256']!=sha(c['_config'])
            or receipt['files'].get(path.name)!=sha(path) or snapshot['source_sha256']!=receipt['source_sha256']):
        raise ValueError('Saved-source snapshot provenance differs')
    checked={};required={}
    from native_recombine import _tool_relative,_tool_inventory
    for filename,expected in receipt['inputs'].items():
        relative=_tool_relative(filename) if Path(filename).suffix=='.py' else None
        if tool_root is not None and relative:
            if not (relative.startswith('src/babylon_js/environment_tools/')
                    or relative in ('src/babylon_js/workflow_tools/inspect_blender.py','src/babylon_js/workflow_tools/uv_audit.py')):
                raise ValueError('Unknown snapshot tool input: '+relative)
            if relative in required and required[relative]!=expected:raise ValueError('Conflicting snapshot tool identity')
            required[relative]=expected
        else:
            if sha(filename)!=expected:raise ValueError('Saved snapshot input/tool changed: '+filename)
            checked[filename]=expected
    if tool_root is not None:checked.update(_tool_inventory(tool_root,required))
    return snapshot,{str(path):sha(path),str(path.parent/'receipt.json'):sha(path.parent/'receipt.json'),**checked}


def _assembly_tool_inputs(tool_root):
    """Current execution tools only; historical stages retain their own authorities."""
    from native_recombine import _tool_relative,_tool_inventory
    paths=list(HERE.glob('*.py'))+[HERE.parent/'ktx_conversion.py',HERE.parent/'workflow_tools/uv_tint_filter.py',
                                HERE.parent/'lightmap_tools/write_linear_exr.py']
    return _tool_inventory(tool_root,{_tool_relative(path):sha(path) for path in paths})


def _tool_authority(manifest,assembly_inputs):
    return {'baseline':manifest['baseline']['tool_source_root'],'local':manifest['local']['tool_source_root'],
        'before_snapshot':manifest['before_snapshot_tool_source_root'],'current_capture':manifest['current']['tool_source_root'],
        'assembly':{'tool_source_root':manifest['assembly']['tool_source_root'],'inputs':assembly_inputs}}


def _neutral_tint(c,old_labels,current_labels,mapping):
    sys.path.insert(0,str(HERE.parent/'workflow_tools'));from uv_tint_filter import decode_rgba8,bilinear
    from PIL import Image
    settings=c['physical_colour_adjustment'];path=(Path(c['_root'])/settings['image']).resolve()
    if sha(path)!=settings['sha256']:raise ValueError('Immutable physical tint image changed')
    with Image.open(path) as image:
        if image.mode!='RGBA':raise ValueError('Physical tint must be actual straight RGBA8 PNG')
        rgba=np.asarray(image)
    decoded=decode_rgba8(rgba,assume_srgb=True,alpha_mode='straight',texture_role='uv2-colour-overlay')
    maximum=0.
    for labels,identities in ((old_labels,mapping['old_affected_ids']),(current_labels,mapping['current_affected_ids'])):
        y,x=np.where(np.isin(labels,identities));uv=np.column_stack(((x+.5)/labels.shape[1],1-(y+.5)/labels.shape[0]))
        alpha=bilinear(decoded,uv,image_origin='top-left')[:,3]
        maximum=max(maximum,float(alpha.max(initial=0)))
    if maximum!=0:raise ValueError('Affected old/new sampled source tint is not neutral; intrinsic transport change is forbidden')
    return {'image_sha256':sha(path),'strength':settings['strength'],'maximum_affected_native_sample_alpha':maximum,
            'limits':'Exact actual owned texel-centre base-level bilinear neutral check, not exhaustive continuous UV coverage'}


def validate_manifest(path):
    path=Path(path).resolve();manifest=read(path)
    if manifest.get('schema_version')!=1 or manifest.get('method')!=METHOD:raise ValueError('Explicit native local patch schema/method required')
    if not (manifest.get('before_snapshot_tool_source_root') and manifest['current'].get('tool_source_root')
            and manifest.get('assembly',{}).get('tool_source_root')):
        raise ValueError('Explicit immutable before/current/assembly tool role roots required')
    baseline=load_config(manifest['baseline']['config']);current=load_config(manifest['current']['config']);local=load_config(manifest['local']['config'])
    validate_configs(baseline,current,local,[row['object'] for row in manifest['affected']])
    from native_recombine import validate_original
    old_plan=validate_original(baseline,manifest['baseline']['candidate'],manifest['baseline']['tool_source_root'])
    local_plan=validate_original(local,manifest['local']['candidate'],manifest['local']['tool_source_root'],
                                 _patch_tool_provenance=manifest['local']['tool_provenance'])
    before,before_inputs=_snapshot_check(manifest['before_snapshot'],baseline,manifest['before_snapshot_tool_source_root'])
    capture=Path(manifest['current']['ids_capture']).resolve();after,after_inputs=_snapshot_check(capture/'snapshot.json',current,manifest['current']['tool_source_root'])
    receipt=read(capture/'receipt.json')
    if receipt.get('kind')!='full-current-native-island-id-capture':raise ValueError('Actual full current ID capture required, not inferred UV raster')
    old=Path(old_plan['original_candidate']);fresh=Path(local_plan['original_candidate'])
    validate_project_metadata(baseline,read(old/'geometry/result.json'))
    for config,geometry in ((current,receipt['geometry']),(local,read(fresh/'geometry/result.json'))):
        validate_project_metadata(config,geometry,sha(baseline['_source']),old)
    for name,expected in receipt['files'].items():
        if Path(name).name!=name or sha(capture/name)!=expected:raise ValueError('Current full ID output changed')
        after_inputs[str(capture/name)]=expected
    old_cat=read(old/'bake/islands.json');current_cat=read(capture/'islands.json');local_cat=read(fresh/'bake/islands.json')
    mapping=validate_layout(before,after,old_cat,current_cat,local_cat,manifest['affected'])
    old_ids=np.load(old/'bake/native_island_ids.npy',allow_pickle=False);new_ids=np.load(capture/'native_island_ids.npy',allow_pickle=False)
    local_ids=np.load(fresh/'bake/native_island_ids.npy',allow_pickle=False)
    old_labels,_=validate_native_ids(old_ids,old_cat);current_labels,_=validate_native_ids(new_ids,current_cat);local_labels,_=validate_native_ids(local_ids,local_cat)
    patch_masks(old_labels,current_labels,local_labels,mapping)
    mapping['current_front_coverage']=front_coverage(after,current_cat,current_labels,manifest['affected'])
    mapping['local_front_coverage']=front_coverage(after,local_cat,local_labels,manifest['affected'])
    neutral=_neutral_tint(current,old_labels,current_labels,mapping)
    inputs={str(path):sha(path),**old_plan['inputs'],**local_plan['inputs'],**before_inputs,**after_inputs,
            current['_source']:sha(current['_source']),current['_config']:sha(current['_config'])}
    inputs.update(_assembly_tool_inputs(manifest['assembly']['tool_source_root']))
    for root in (old,fresh):
        for name in ARRAYS+('masters/albedo_uv.exr',):
            filename=(root/name).resolve()
            if str(filename) not in inputs:raise ValueError('Patch measurement lacks completed-output hash proof: '+name)
    return manifest,current,mapping,(old_labels,current_labels,local_labels),inputs,neutral


def _albedo(path):
    # Same installed OpenCV EXR backend as delivery preparation. OIIO belongs
    # only to the explicitly selected EXR writer interpreter, not the caller.
    # This is a standalone dependency: importing the Blender package in external
    # Python needs bpy. Load the file directly without exposing babylon_js/logging.py
    # as a top-level stdlib logging module through sys.path.
    from importlib.util import spec_from_file_location,module_from_spec
    spec=spec_from_file_location('bjs_native_patch_ktx_conversion',HERE.parent/'ktx_conversion.py')
    converter=module_from_spec(spec);spec.loader.exec_module(converter)
    raw=converter.read_float_hdr(path)
    if raw.shape[2]!=4:raise ValueError('Actual linear albedo RGBA required')
    if raw.dtype!=np.float32 or not np.isfinite(raw).all():raise ValueError('Actual finite float32 linear albedo RGBA required')
    return np.ascontiguousarray(raw[:,:,[2,1,0,3]])


def _fields(manifest,mapping,labels):
    old=Path(manifest['baseline']['candidate']);fresh=Path(manifest['local']['candidate'])
    for relative in ARRAYS:
        yield relative,assemble_array(np.load(old/relative,allow_pickle=False),np.load(fresh/relative,allow_pickle=False),*labels,mapping)
    yield 'albedo_owned.npy',assemble_array(_albedo(old/'masters/albedo_uv.exr'),_albedo(fresh/'masters/albedo_uv.exr'),*labels,mapping)


def assemble(manifest_path,output,image_python):
    manifest,current,mapping,labels,inputs,neutral=validate_manifest(manifest_path)
    output=Path(output).resolve()
    if output.exists():raise ValueError('Use a new immutable patch candidate directory')
    for key in ('baseline','local'):
        original=Path(manifest[key]['candidate']).resolve()
        if output.is_relative_to(original) or original.is_relative_to(output):raise ValueError('Patch output must be separate from original measured candidates')
    output.mkdir(parents=True);outputs={};checks=[]
    def array(path,data):
        target=output/path;target.parent.mkdir(parents=True,exist_ok=True)
        with target.open('xb') as stream:np.save(stream,data,allow_pickle=False)
        outputs[path]=sha(target)
    from ownership import dilate
    current_labels=labels[1];unrelated=patch_masks(*labels,mapping)[2]
    for relative,data in _fields(manifest,mapping,labels):
        array(relative,data);checks.append({'field':relative,'unrelated_owned_pixels':int(unrelated.sum()),'unrelated_owned_bit_exact':True})
        for name,source in MASTERS.items():
            if source==relative:
                target='masters/'+Path(name).with_suffix('.npy').name;array(target,dilate(data,current_labels))
    ids=np.ones(current_labels.shape+(4,),np.float32);ids[:,:,:3]=current_labels[...,None]
    array('masters/island_ids.npy',dilate(ids,current_labels));array('ownership/labels.npy',current_labels)
    capture=Path(manifest['current']['ids_capture']);(output/'bake').mkdir(exist_ok=True)
    for name in ('native_island_ids.npy','islands.json'):
        shutil.copyfile(capture/name,output/'bake'/name);outputs['bake/'+name]=sha(output/'bake'/name)
    writer=HERE.parent/'lightmap_tools/write_linear_exr.py';inputs[str(Path(image_python).resolve())]=sha(image_python)
    subprocess.run([str(image_python),str(writer)]+[str(output/'masters'/Path(name).with_suffix('.npy').name) for name in list(MASTERS)+['island_ids.exr']],check=True)
    for name in list(MASTERS)+['island_ids.exr']:outputs['masters/'+name]=sha(output/'masters'/name)
    geometry=read(capture/'receipt.json')['geometry'];(output/'geometry').mkdir();save_json(output/'geometry/result.json',geometry)
    for relative,value in (('response/result.json',{'schema_version':1,'kind':'assembled-baseline-plus-measured-local-response',
            'source_sha256':sha(current['_source']),'geometry_sha256':geometry['geometry_sha256'],'originals':{key:manifest[key]['candidate'] for key in ('baseline','local')}}),
            ('ownership/result.json',{'method':'native-island-id','kind':METHOD,'owner_inference':False,'mapping':mapping}),
            ('masters/albedo_uv.exr.json',{'schema_version':1,'kind':METHOD,'source_sha256':sha(current['_source']),'geometry_sha256':geometry['geometry_sha256']})):
        target=output/relative;target.parent.mkdir(parents=True,exist_ok=True);save_json(target,value);outputs[relative]=sha(target)
    outputs['geometry/result.json']=sha(output/'geometry/result.json')
    native={'schema_version':1,'kind':'assembled-native-local-island-patch','method':'native-island-id',
        'source_sha256':sha(current['_source']),'config_sha256':sha(current['_config']),'config_path':current['_config'],
        'scene':current['scene'],'size':current['size'],'uv':current.get('uv','SimpleBake'),'geometry_sha256':geometry['geometry_sha256'],
        'catalogue_file_sha256':sha(output/'bake/islands.json'),'catalogue_identity_sha256':catalogue_identity(read(output/'bake/islands.json')),
        'native_ids_sha256':sha(output/'bake/native_island_ids.npy'),'raw_sha256':{mode:sha(output/'bake'/(mode+'_raw.npy')) for mode in ('direct','indirect')},
        'actual_owner_capture':str(capture/'receipt.json'),'actual_owner_capture_sha256':sha(capture/'receipt.json'),
        'radiance_provenance':'Unrelated owned samples retained from immutable baseline; only authorized chart samples are fresh local measurements. No fresh full-room bake claimed.'}
    save_json(output/'bake/native_ownership.provenance.json',native);outputs['bake/native_ownership.provenance.json']=sha(output/'bake/native_ownership.provenance.json')
    receipt={'schema_version':1,'kind':METHOD,'manifest':str(Path(manifest_path).resolve()),'source_sha256':sha(current['_source']),
        'config_sha256':sha(current['_config']),'config':current['_config'],'geometry_sha256':geometry['geometry_sha256'],
        'inputs':inputs,'outputs':outputs,'mapping':mapping,'checks':checks,'neutral_tint':neutral,
        'tool_authority':_tool_authority(manifest,_assembly_tool_inputs(manifest['assembly']['tool_source_root'])),
        'source_saved':False,'fresh_full_room_bake':False,'limits':native['radiance_provenance']+' Nonowned gap padding is regenerated with ordinary dilation.'}
    save_json(output/'native_local_patch.provenance.json',receipt)
    validate_assembly(output)
    return receipt


def validate_assembly(root):
    root=Path(root).resolve();record=read(root/'native_local_patch.provenance.json')
    if record.get('kind')!=METHOD or record.get('schema_version')!=1 or record.get('fresh_full_room_bake') is not False:raise ValueError('Explicit truthful local patch receipt required')
    for name,expected in record['inputs'].items():
        if sha(name)!=expected:raise ValueError('Original/local/current patch input changed: '+name)
    for name,expected in record['outputs'].items():
        path=(root/name).resolve()
        if not path.is_relative_to(root) or sha(path)!=expected:raise ValueError('Assembled patch output changed: '+name)
    manifest,current,mapping,labels,_,neutral=validate_manifest(record['manifest'])
    if record.get('tool_authority')!=_tool_authority(manifest,_assembly_tool_inputs(manifest['assembly']['tool_source_root'])):
        raise ValueError('Assembly tool role authority differs')
    if (record['source_sha256']!=sha(current['_source']) or record['config_sha256']!=sha(current['_config']) or mapping!=record['mapping'] or neutral!=record['neutral_tint']):raise ValueError('Patch assembly identity/mapping changed')
    from ownership import dilate
    for relative,expected in _fields(manifest,mapping,labels):
        actual=np.load(root/relative,allow_pickle=False)
        if not np.array_equal(actual.view(np.uint32),expected.view(np.uint32)):raise ValueError('Assembled field violates exact measured reconstruction: '+relative)
        for name,source in MASTERS.items():
            if source==relative and not np.array_equal(_albedo(root/'masters'/name).view(np.uint32),dilate(expected,labels[1]).view(np.uint32)):
                raise ValueError('Assembled master differs from ordinary ownership dilation: '+name)
    if not np.array_equal(np.load(root/'ownership/labels.npy'),labels[1]):raise ValueError('Assembled labels differ from actual current ownership')
    validate_native_ids(np.load(root/'bake/native_island_ids.npy'),read(root/'bake/islands.json'),
        np.load(root/'bake/direct_raw.npy'),np.load(root/'bake/indirect_raw.npy'),np.load(root/'ao/ao_raw.npy'))
    return record


def validate_for_recombine(c,root,tool_root):
    """Explicit patch branch; never reinterpret this as an ordinary fresh build."""
    record=validate_assembly(root);current=read(record['config']);trial=read(c['_config'])
    if trial!=current or sha(c['_source'])!=record['source_sha256'] or c.get('finishing_overrides'):
        raise ValueError('Measured local assembly downstream recipe/source must be exact current source; no finishing trials')
    tool_root=Path(tool_root).resolve()
    authority=record['tool_authority']['assembly']
    if Path(authority['tool_source_root']).resolve()!=tool_root or authority['inputs']!=_assembly_tool_inputs(tool_root):
        raise ValueError('Patch assembly execution tool source differs')
    copies=['geometry/result.json','bake/islands.json','ownership/labels.npy','ownership/result.json','response/result.json',
            'bake/native_ownership.provenance.json','bake/native_island_ids.npy','bake/direct_raw.npy','bake/indirect_raw.npy','ao/ao_raw.npy']
    copies+=['masters/'+name for name in list(MASTERS)+['island_ids.exr']]+['masters/albedo_uv.exr.json']
    inputs={**record['inputs'],**{str((Path(root)/name).resolve()):expected for name,expected in record['outputs'].items()},
            str((Path(root)/'native_local_patch.provenance.json').resolve()):sha(Path(root)/'native_local_patch.provenance.json')}
    return {'schema_version':1,'kind':'exact-source-native-recombine','assembly_kind':METHOD,'original_candidate':str(Path(root).resolve()),
        'source_sha256':record['source_sha256'],'geometry_sha256':record['geometry_sha256'],'scene':c['scene'],'size':c['size'],'uv':c.get('uv','SimpleBake'),
        'inputs':inputs,'copies':copies,'finishing_overrides':{},'physical_source_unchanged':True,'fresh_full_room_bake':False,
        'limits':record['limits'],'native_local_patch_provenance_sha256':sha(Path(root)/'native_local_patch.provenance.json')}


def _rna_json_value(data,field):
    """Retain RNA scalar values and recursively preserve numeric container order."""
    if isinstance(data,(bool,int,float,str)):return data
    if isinstance(data,(dict,set,bytes,bytearray)):
        raise TypeError('Unsupported RNA field '+field+': '+type(data).__name__)
    try:values=iter(data)
    except TypeError as error:
        raise TypeError('Unsupported RNA field '+field+': '+type(data).__name__) from error
    return [_rna_json_value(item,field+'['+str(index)+']') for index,item in enumerate(values)]


def _scalar_state(value):
    ignored={'rna_type','name','name_full','active_material_index','color','display_type','hide_viewport','hide_select','show_name','show_axis','show_wire','show_all_edges'}
    result={}
    for prop in value.bl_rna.properties:
        if prop.identifier in ignored or prop.is_readonly or prop.type not in ('BOOLEAN','INT','FLOAT','STRING','ENUM'):continue
        data=getattr(value,prop.identifier)
        if prop.type=='ENUM':
            # Blender EnumProperty has no is_array; flags are a set of exact labels.
            result[prop.identifier]=sorted(data) if isinstance(data,set) else _rna_json_value(data,prop.identifier)
        else:
            # Actual values can be Vector/Euler/Quaternion even when RNA array
            # metadata is absent; Matrix rows also need recursive conversion.
            result[prop.identifier]=_rna_json_value(data,prop.identifier)
    return result


def source_snapshot(c):
    """Reuse installed evaluated UV snapshot; add physical state before any relink/tint/proxy."""
    import bpy
    from worker import select
    from appearance import preview_identity
    from babylon_js.workflow_tools.inspect_blender import snapshot_uv2
    transport=select(c,'receivers')+select(c,'contributors');names=sorted(obj.name for obj in transport)
    uv=snapshot_uv2(names,uv=c.get('uv','SimpleBake'),expected_scene=c['scene'])
    physical=transport+select(c,'glass')+select(c,'visible_emitters');materials={slot.material for obj in physical for slot in obj.material_slots if slot.material}
    preview=bpy.data.materials[c['preview_material']];materials.add(preview)
    invariants={'geometry_uv1':uv['provenance']['geometry_uv1_identity_sha256'],
        'objects':{},'materials':{material.name:{'shader':preview_identity(material),'settings':_scalar_state(material)} for material in materials},
        'world':{'shader':preview_identity(bpy.context.scene.world),'settings':_scalar_state(bpy.context.scene.world)},
        'source_images':{relative:sha(Path(c['_root'])/relative) for relative in c['source_images']}}
    deps=bpy.context.evaluated_depsgraph_get()
    for obj in bpy.context.scene.objects:
        state={'settings':_scalar_state(obj),'matrix':[list(row) for row in obj.matrix_world],
               'materials':[(slot.link,slot.material.name if slot.material else None) for slot in obj.material_slots]}
        if obj.type=='MESH' and obj.name not in names:
            evaluated=obj.evaluated_get(deps);mesh=evaluated.to_mesh(preserve_all_data_layers=True,depsgraph=deps)
            try:
                state['unselected_geometry_uv']=digest({'vertices':[list(v.co) for v in mesh.vertices],
                    'loops':[loop.vertex_index for loop in mesh.loops],'polygons':[(p.loop_start,p.loop_total,p.use_smooth,p.material_index) for p in mesh.polygons],
                    'normals':[list(n.vector) for n in mesh.corner_normals],
                    'uv':{layer.name:[list(v.uv) for v in layer.data] for layer in mesh.uv_layers}})
            finally:evaluated.to_mesh_clear()
        elif obj.type in ('LIGHT','CAMERA'):state['data']=_scalar_state(obj.data)
        invariants['objects'][obj.name]=state
    from babylon_js import colour_adjustment as ca
    controls=ca.controls(bpy.context.scene)
    invariants['saved_controls']={s.name:list(s.default_value) if hasattr(s.default_value,'__len__') else s.default_value
        for s in controls.inputs if hasattr(s,'default_value') and not s.is_linked} if controls else None
    return {'schema_version':1,'kind':'native-local-patch-source-snapshot','source_sha256':sha(c['_source']),'uv':uv,'invariants':invariants}


def capture_worker(c,output,ids):
    import bpy
    from physical_colour_adjustment import select_scene
    from worker import setup,evaluate_receivers
    from ao import geometry_identity
    from native_ownership import bake_native_ids
    if not bpy.app.background or Path(bpy.data.filepath).resolve()!=Path(c['_source']):raise ValueError('Saved source factory background only')
    select_scene(c);output=Path(output);snapshot=source_snapshot(c);save_json(output/'snapshot.json',snapshot)
    inputs={c['_source']:sha(c['_source']),c['_config']:sha(c['_config'])}
    for path in list(HERE.glob('*.py'))+[HERE.parent/'workflow_tools/inspect_blender.py',HERE.parent/'workflow_tools/uv_audit.py']:
        inputs[str(path.resolve())]=sha(path)
    for relative in c['source_images']:inputs[str((Path(c['_root'])/relative).resolve())]=sha(Path(c['_root'])/relative)
    geometry=geometry_identity(c);settings=None;files={'snapshot.json':sha(output/'snapshot.json')}
    if ids:
        if c.get('contributors',{}).get('names'):raise ValueError('Full current native ID capture requires full receiver scope, no contributor role')
        setup(c);copies,catalogue=evaluate_receivers(c);copy_names=[obj.name for obj in copies]
        try:
            bpy.ops.object.select_all(action='DESELECT')
            for obj in copies:obj.hide_set(False);obj.select_set(True)
            bpy.context.view_layer.objects.active=copies[0];bpy.ops.object.join();proxy=bpy.context.object
            image,settings=bake_native_ids(c,proxy,catalogue)
            np.save(output/'native_island_ids.npy',image,allow_pickle=False);save_json(output/'islands.json',catalogue)
            files.update({name:sha(output/name) for name in ('native_island_ids.npy','islands.json')})
        finally:
            for name in copy_names:
                obj=bpy.data.objects.get(name)
                if obj is not None:bpy.data.objects.remove(obj,do_unlink=True)
    for path,expected in inputs.items():
        if sha(path)!=expected:raise ValueError('ID/snapshot source/tool changed')
    save_json(output/'receipt.json',{'schema_version':1,'kind':'full-current-native-island-id-capture' if ids else 'saved-local-patch-source-snapshot',
        'source_sha256':sha(c['_source']),'config_sha256':sha(c['_config']),'geometry':geometry,'inputs':inputs,'files':files,
        'settings':settings,'native_radiance_baked':False,'source_saved':False})


def main():
    parser=argparse.ArgumentParser(description=__doc__);commands=parser.add_subparsers(dest='command',required=True)
    for name in ('snapshot','capture-ids','snapshot-worker','capture-ids-worker'):
        sub=commands.add_parser(name);sub.add_argument('--config',type=Path,required=True);sub.add_argument('--output',type=Path,required=True)
        if not name.endswith('worker'):sub.add_argument('--blender',type=Path,required=True)
    sub=commands.add_parser('assemble');sub.add_argument('--manifest',type=Path,required=True);sub.add_argument('--output',type=Path,required=True);sub.add_argument('--image-python',type=Path,required=True)
    sub=commands.add_parser('verify');sub.add_argument('--candidate',type=Path,required=True)
    args=parser.parse_args()
    if args.command=='assemble':assemble(args.manifest,args.output,args.image_python)
    elif args.command=='verify':validate_assembly(args.candidate)
    elif args.command.endswith('worker'):capture_worker(load_config(args.config),args.output,args.command=='capture-ids-worker')
    else:
        c=load_config(args.config);output=args.output.resolve()
        if output.exists():raise ValueError('Use a new immutable snapshot/ID capture directory')
        output.mkdir(parents=True)
        argv=[str(args.blender),'--background','--factory-startup',c['_source'],'--python-exit-code','1','--python',str(Path(__file__).resolve()),'--',
              args.command+'-worker','--config',str(args.config.resolve()),'--output',str(output)]
        with (output/'blender.log').open('w',encoding='utf8') as stream:subprocess.run(argv,stdout=stream,stderr=subprocess.STDOUT,check=True)


if __name__=='__main__':
    if '--' in sys.argv:sys.argv=sys.argv[sys.argv.index('--'):]
    main()
