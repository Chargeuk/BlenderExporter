"""Opt-in two-context assembly: convert prepared geometry, then validate volume on untouched source."""
import argparse
from contextlib import contextmanager
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROUTE='separate-source-v1'
VOLUME_KEY='kadshowEnvironmentLightingVolume'
HERE=Path(__file__).resolve().parent
if str(HERE) not in sys.path:sys.path.insert(0,str(HERE))
if str(HERE.parent.parent) not in sys.path:sys.path.insert(0,str(HERE.parent.parent))

def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()
def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def write(path,value):Path(path).write_text(json.dumps(value,indent=2,allow_nan=False)+'\n',encoding='utf8')
def require(test,message):
    if not test:raise ValueError(message)
def checked_path(root,relative):
    path=(Path(root)/relative).resolve()
    require(path.is_relative_to(Path(root).resolve()) and path.is_file(),'Missing/escaping assembly dependency: '+str(path))
    return path
def source_path(config,path):return (Path(config.get('package_root',Path(path).resolve().parent))/config['source']).resolve()
def route(config):require(config.get('volume_export_route')==ROUTE,'Unsupported separate-source volume route')

@contextmanager
def without_saved_volume(world,config):
    """Suppress only this optional feature in disposable Stage A; restore on every outcome."""
    if config.get('volume_export_route') is None:
        yield
        return
    route(config)
    import bpy
    require(bpy.app.background,'Separate-source Stage A requires disposable background Blender')
    key='bjs_environment_lighting_volume_config';present=key in world;previous=world.get(key)
    if present:del world[key]
    try:yield
    finally:
        if present:world[key]=previous
        elif key in world:del world[key]

def marker(model):
    matches=[n for n in model.get('meshes',[])if n.get('name','').lower()=='hasenv']
    require(len(matches)==1 and not matches[0].get('positions') and not matches[0].get('indices'),'Volume assembly requires one empty hasenv mesh')
    metadata=matches[0].get('metadata',{})
    require(isinstance(metadata,dict) and VOLUME_KEY not in metadata,'Stage A must have valid metadata and no existing volume')
    return matches[0]

def verify_stage_a(stage,config,source_hash):
    """Use actual conversion outputs, never a flag that substitutes for volume source validation."""
    stage=Path(stage).resolve();model=checked_path(stage,config['model'])
    report_path=checked_path(stage,Path(config['model']).stem+'.ktx-report.json')
    report=read(report_path);selection=read(checked_path(stage,'selection-settings.json'))
    require(report.get('status')=='passed' and selection.get('source_sha256')==source_hash,'Stage A source/conversion mismatch')
    require(selection.get('volume_export_route')==ROUTE,'Stage A was not explicitly separate-source conversion')
    require(not report.get('environment_lighting_volume') and not report.get('options',{}).get('environment_lighting_volume'),'Stage A must omit volume export')
    env=report.get('options',{}).get('environment')
    require(isinstance(env,dict),'Stage A must include ENV conversion')
    require(all(env.get(k,v)==v for k,v in (('exposure',0),('highlight_gain',1),('highlight_threshold',1))),'Stage A ENV must use neutral accepted conversion settings')
    require(report.get('environment',{}).get('source_sha256')==selection.get('capture_sha256'),'Stage A capture/ENV source mismatch')
    records={}
    for row in report.get('delivery_files',[]):
        path=Path(row['path']).resolve()
        require(path.is_relative_to(stage) and path.is_file() and sha(path)==row['sha256'],'Stage A delivery bytes changed: '+str(path))
        relative=path.relative_to(stage).as_posix();require(relative not in records,'Duplicate Stage A file record');records[relative]=row['sha256']
    require(config['model'] in records and 'environment.env' in records,'Stage A closure lacks model/ENV')
    data=read(model);node=marker(data)
    expected=report.get('options',{}).get('environment_lighting_defaults')
    import math
    if expected is not None:
        require(isinstance(expected,dict) and type(expected.get('version'))is int and expected.get('version')==1 and expected.get('mode') in ('off','simple','advanced') and
            isinstance(expected.get('multiplier'),(int,float)) and not isinstance(expected.get('multiplier'),bool) and math.isfinite(expected['multiplier']) and 0<=expected['multiplier']<=10,'Invalid Stage A file defaults')
    require(node.get('metadata',{}).get('kadshowEnvironmentLightingDefaults')==expected,'Stage A file defaults mismatch')
    return dict(kind='converted-stage-a-without-volumes',source_sha256=source_hash,files=records,
                model_sha256=sha(model),conversion_report_sha256=sha(report_path),selection_sha256=sha(stage/'selection-settings.json'),
                environment_options=env,capture_sha256=selection['capture_sha256'])

def verify_boundary(stage,config,config_path,source_hash):
    boundary=read(checked_path(stage,'STAGE_A_BOUNDARY.json'))
    require(boundary.get('config_sha256')==sha(config_path) and boundary.get('source_sha256')==source_hash,'Stage A boundary source/config changed')
    current=verify_stage_a(stage,config,source_hash)
    require(current==boundary.get('conversion'),'Stage A model, ENV, selection or conversion report changed')
    return boundary,current

def metadata_only_delta(before,after):
    original=copy.deepcopy(before);marker(original)
    candidate=copy.deepcopy(after)
    matches=[n for n in candidate.get('meshes',[])if n.get('name','').lower()=='hasenv']
    require(len(matches)==1 and isinstance(matches[0].get('metadata'),dict),'Invalid assembled hasenv metadata')
    volume=matches[0]['metadata'].pop(VOLUME_KEY,None)
    require(isinstance(volume,dict),'Volume API did not attach metadata')
    # An absent metadata dict may be created solely to hold the optional descriptor.
    before_node=next(n for n in original['meshes']if n.get('name','').lower()=='hasenv')
    if 'metadata' not in before_node and not matches[0]['metadata']:del matches[0]['metadata']
    require(candidate==original,'Stage B changed model data outside the volume metadata')
    require(volume.get('strength')==1 and not isinstance(volume.get('strength'),bool) and volume.get('advanced',{}).get('strength')==1 and not isinstance(volume.get('advanced',{}).get('strength'),bool),'Assembly requires Simple and Advanced strength one')
    return volume

def package_closure(model,package):
    from babylon_js import ktx_conversion as core
    refs={owner[key]for owner,key,_,_ in core.texture_fields(model)}
    refs.add('environment.env')
    if any(n.get('name','').lower()=='hasskyboxbasis'for n in model.get('meshes',[])):refs.add('cubemap.basis')
    node=next(n for n in model['meshes']if n.get('name','').lower()=='hasenv')
    desc=node['metadata'][VOLUME_KEY];refs.update((desc['texture'],desc['advanced']['texture']))
    return {name:sha(checked_path(package,name))for name in sorted(refs)}

def resolve_stage_b_options(config,config_path,saved):
    """Explicit, bound clean-source opt-in; never change a World association.

    Saved-only exports retain their previous behavior. The public volume producer
    still validates the source/receipt/appearance/reference in the untouched context.
    """
    ktx=config.get('ktx_options',{})
    require(isinstance(ktx,dict),'Invalid KTX options')
    if 'environment_lighting_volume' not in ktx:
        require(saved is not None,'Missing saved or explicit bound volume association')
        return saved
    explicit=ktx['environment_lighting_volume']
    required={'config','image','strength'}
    allowed=required|{'simple_divisor_mode','advanced_alpha_policy'}
    require(isinstance(explicit,dict) and required<=set(explicit)<=allowed,'Explicit volume requires config/image/strength and supported policies')
    require(type(explicit['strength']) in (int,float) and explicit['strength']==1,'Explicit assembly volume strength must be one')
    require(explicit.get('simple_divisor_mode','per-pixel') in ('per-pixel','block-4x4'),'Unsupported explicit Simple divisor mode')
    require(explicit.get('advanced_alpha_policy') in (None,'source-ceiling'),'Unsupported explicit Advanced alpha policy')
    root=Path(config.get('package_root',Path(config_path).resolve().parent)).resolve()
    if __package__:
        from .recipe_cli import resolve_input
    else:
        from recipe_cli import resolve_input
    result=dict(explicit)
    for key in ('config','image'):
        value=explicit[key]
        require(isinstance(value,str) and bool(value),'Explicit volume path must be nonempty: '+key)
        path=resolve_input(root,config,value)
        matches=[row for row in config.get('bound_dependencies',[]) if resolve_input(root,config,row['path'])==path]
        require(bool(matches) and all(row.get('sha256')==sha(path) for row in matches),'Unbound or changed explicit volume input: '+key)
        result[key]=str(path)
    if __package__:
        from .volume_relocation import package_paths
    else:
        from volume_relocation import package_paths
    record=read(result['config']);record_source=package_paths(record,Path(result['config']))
    require(Path(record_source['scene']).resolve()==source_path(config,config_path) and
        record_source['sceneSha256']==config['source_sha256'] and record.get('masterSha256')==sha(result['image']),
        'Explicit volume receipt source/master differs from the immutable assembly source')
    if saved is not None:
        # Blender-relative saved paths are relative to the immutable source, not a scratch output.
        source=source_path(config,config_path)
        def saved_path(value):
            require(isinstance(value,str) and bool(value),'Conflicting empty saved volume path')
            return ((source.parent/value[2:]) if value.startswith('//') else (source.parent/value)).resolve()
        saved_receipt=saved_path(saved['config'])
        saved_image=saved_path(saved['image']) if saved.get('image') else (saved_receipt.parent/read(saved_receipt)['master']).resolve()
        require(saved_receipt==Path(result['config']) and saved_image==Path(result['image']) and
            type(saved.get('strength',1)) in (int,float) and saved.get('strength',1)==1 and
            saved.get('simple_divisor_mode','per-pixel')==result.get('simple_divisor_mode','per-pixel') and
            saved.get('advanced_alpha_policy')==result.get('advanced_alpha_policy'),
            'Saved and explicit volume associations conflict')
    return result

def assemble(config_path,stage_a,output):
    """Called only in a second background process opened on the untouched saved scene."""
    import bpy
    from babylon_js.environment_lighting_volume import export_volume,saved_options
    config_path=Path(config_path).resolve();config=read(config_path);route(config)
    from recipe_cli import validate_volume_export_routes
    validate_volume_export_routes(config)
    source=source_path(config,config_path)
    require(bpy.app.background and Path(bpy.data.filepath).resolve()==source,'Open the exact untouched saved source for Stage B')
    require(not bpy.data.is_dirty and not any(i.is_dirty for i in bpy.data.images),'Stage B requires a clean saved source')
    source_hash=sha(source);require(source_hash==config['source_sha256'],'Stage B saved source changed')
    boundary,conversion=verify_boundary(stage_a,config,config_path,source_hash)
    associated=Path(bpy.path.abspath(bpy.context.scene.world.get('bjs_environment_image',''))).resolve()
    require(associated==Path(bpy.path.abspath(conversion['environment_options']['image'])).resolve() and sha(associated)==conversion['capture_sha256'],'Stage B ENV reference differs from Stage A')
    options=resolve_stage_b_options(config,config_path,saved_options(bpy.context.scene.world))
    output=Path(output).resolve();require(not output.exists(),'Stage B output must be fresh');output.mkdir(parents=True)
    package=output/'package';package.mkdir();work=output/'work';work.mkdir()
    before=read(Path(stage_a)/config['model']);marker(before)
    for relative,digest in conversion['files'].items():
        original=checked_path(stage_a,relative);target=package/relative;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(original,target)
        require(sha(target)==digest,'Stage A copy changed')
    model=copy.deepcopy(before)
    # This real API rechecks the original portable receipt and full scene identity in this untouched context.
    volume_report=export_volume(bpy.context,model,work,package,options,config['tools']['ktx'],
        config['preparation']['operator_settings'].get('ktx_threads',8),conversion['environment_options'])
    metadata_only_delta(before,model)
    from babylon_js import ktx_conversion as core
    # Reports stay indented; only the delivered model uses compact JSON.
    (package/config['model']).write_text(core.model_json(model,ensure_ascii=True,allow_nan=False),encoding='utf8')
    closure=package_closure(model,package)
    require(sha(source)==source_hash,'Stage B changed the saved source')
    require(verify_boundary(stage_a,config,config_path,source_hash)[0]==boundary,'Stage A boundary changed during assembly')
    result=dict(kind='separate-source-volume-assembly-v1',status='assembled',source_sha256=source_hash,
        config_sha256=sha(config_path),stage_a=dict(path=str(Path(stage_a).resolve()),boundary_sha256=sha(Path(stage_a)/'STAGE_A_BOUNDARY.json'),
            immutable_conversion_report_sha256=conversion['conversion_report_sha256'],model_sha256=conversion['model_sha256']),
        stage_b=dict(api='environment_lighting_volume.export_volume',report=volume_report,model_delta='only hasenv.metadata.kadshowEnvironmentLightingVolume'),
        model=str(package/config['model']),model_sha256=sha(package/config['model']),closure=closure,
        final_files={p.relative_to(package).as_posix():sha(p)for p in package.rglob('*')if p.is_file()},source_saved=False,
        limits='Two separately proved stages; original Stage A conversion report does not describe the assembled model bytes. Numeric/visual/runtime acceptance is separate.')
    write(output/'assembly-report.json',result)
    print('SEPARATE_SOURCE_VOLUME_ASSEMBLED '+str(output/'assembly-report.json'),flush=True)
    return result

def run_pipeline(config_path,output,blender):
    config_path=Path(config_path).resolve();config=read(config_path);route(config)
    from recipe_cli import validate_volume_export_routes
    validate_volume_export_routes(config)
    source=source_path(config,config_path);source_hash=sha(source);config_hash=sha(config_path);require(source_hash==config['source_sha256'],'Saved assembly source changed')
    output=Path(output).resolve();require(not output.exists() and not output.is_relative_to(config_path.parent),'Use fresh scratch assembly output');output.mkdir(parents=True)
    flags={'creationflags':subprocess.CREATE_NO_WINDOW}if os.name=='nt'else{}
    stage_a=output/'stage-a';stage_b=output/'stage-b'
    commands=[[str(blender),'-b',str(source),'--python-exit-code','1','--python',str(HERE/'recipe_cli.py'),'--','export','--config',str(config_path),str(stage_a)],
              [str(blender),'-b',str(source),'--python-exit-code','1','--python',str(Path(__file__).resolve()),'--','--config',str(config_path),'--stage-a',str(stage_a),'--output',str(stage_b)]]
    with (output/'stage-a.log').open('w',encoding='utf8')as stream:subprocess.run(commands[0],stdout=stream,stderr=subprocess.STDOUT,check=True,**flags)
    require(sha(config_path)==config_hash,'Assembly configuration changed during Stage A')
    conversion=verify_stage_a(stage_a,config,source_hash)
    write(stage_a/'STAGE_A_BOUNDARY.json',dict(config_sha256=config_hash,source_sha256=source_hash,conversion=conversion))
    require(sha(source)==source_hash,'Stage A changed the saved source')
    with (output/'stage-b.log').open('w',encoding='utf8')as stream:subprocess.run(commands[1],stdout=stream,stderr=subprocess.STDOUT,check=True,**flags)
    require(sha(source)==source_hash,'Assembly changed the saved source')
    write(output/'pipeline.json',dict(kind=ROUTE,commands=commands,source_sha256=source_hash,
        config_sha256=sha(config_path),assembly_report=str(stage_b/'assembly-report.json')))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--config',required=True,type=Path);p.add_argument('--stage-a',required=True,type=Path);p.add_argument('--output',required=True,type=Path)
    a=p.parse_args(sys.argv[sys.argv.index('--')+1:]);assemble(a.config,a.stage_a,a.output)
