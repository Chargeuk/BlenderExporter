"""Focused release compatibility check; no Blender or install mutation.

Compare release-owned files before installation and verify built ZIP contents.
Locally configured converter state is inventoried separately and never packaged.
"""
import argparse
import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import zipfile

parser=argparse.ArgumentParser()
parser.add_argument('--source',type=Path,required=True)
parser.add_argument('--installed',type=Path,required=True)
parser.add_argument('--archive',type=Path,required=True)
parser.add_argument('--receipt',type=Path,required=True)
parser.add_argument('--installed-current',action='store_true')
parser.add_argument('--preserve-receipt',type=Path)
parser.add_argument('--profile',choices=('3.3.34','3.3.35'),default='3.3.34',
                    help='Explicit source release; historical 3.3.34 checks remain the default')
args=parser.parse_args()

def sha(data):return hashlib.sha256(data).hexdigest()
def inventory(root):
    owned={};local={}
    for current,dirs,files in os.walk(root):
        dirs[:]=sorted(n for n in dirs if n.lower() not in ('node_modules','__pycache__','.git'))
        for name in files:
            file=Path(current)/name;relative=file.relative_to(root).as_posix()
            if name.lower() in ('runtime.local.json','setup.local.log'):
                local[relative]=sha(file.read_bytes());continue
            if file.suffix.lower() in ('.pyc','.pyo','.md'):continue
            owned[relative]=sha(file.read_bytes())
    return owned,local

PROFILES={
    '3.3.34':{'files':{'__init__.py','environment_tools/camera_views.py'},'changed':{},'added':{}},
    '3.3.35':{
        'files':{'__init__.py','environment_tools/camera_views.py','environment_tools/cli.py',
                 'workflow_tools/native_view_diagnostics.py'},
        'changed':{
            'environment_tools/camera_views.py':{'validate_camera_set','_capture_modes','capture_views'},
            'environment_tools/cli.py':{'main'},
            'workflow_tools/native_view_diagnostics.py':{'diffuse_passes'}},
        'added':{'environment_tools/camera_views.py':{'_physical_diffuse_state','_capture_views','physical_diffuse_main'}}}}
profile=PROFILES[args.profile]
source,_=inventory(args.source);installed,local=inventory(args.installed)
allowed=profile['files']
differences=[{'path':name,'source':source.get(name),'installed':installed.get(name)}
             for name in sorted(set(source)|set(installed)) if source.get(name)!=installed.get(name)]
unexpected=[row for row in differences if row['path'] not in allowed]
assert not unexpected, 'Unexpected source/installed replacement differences: '+json.dumps(unexpected)
expected=set() if args.installed_current else allowed
assert {row['path'] for row in differences}==expected, 'Installed release differences do not match this validation phase'
if args.preserve_receipt:
    prior=json.loads(args.preserve_receipt.read_text(encoding='utf8'))
    assert prior['preserve_local_state']==local, 'Configured local converter state changed during installation'
    assert prior.get('profile','3.3.34')==args.profile, 'Preservation receipt belongs to a different release profile'
def dump(node):return ast.dump(node,include_attributes=False)
def functions(path):
    return {node.name:node for node in ast.parse(path.read_text(encoding='utf8')).body
            if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef))}
def assert_calls(node,names):
    actual={dump(item.func) for item in ast.walk(node) if isinstance(item,ast.Call)}
    assert all(dump(ast.parse(name,mode='eval').body) in actual for name in names), 'Required opt-in helper call absent: '+node.name
def remove_exact_statements(node,texts):
    """Reverse only enumerated additive statements, then compare the whole AST."""
    for text in texts:
        expected=dump(ast.parse(text).body[0]);count=0
        for parent in ast.walk(node):
            for key,value in ast.iter_fields(parent):
                if isinstance(value,list):
                    kept=[]
                    for item in value:
                        if isinstance(item,ast.stmt) and dump(item)==expected:count+=1
                        else:kept.append(item)
                    setattr(parent,key,kept)
        assert count==1, 'Expected exactly one release addition: '+text

init=ast.parse((args.source/'__init__.py').read_text(encoding='utf8'))
metadata=ast.literal_eval(init.body[0].value)
assert metadata['version']==tuple(map(int,args.profile.split('.'))), 'Source version differs from explicit release profile'
if args.profile=='3.3.35' and not args.installed_current:
    installed_init=ast.parse((args.installed/'__init__.py').read_text(encoding='utf8'))
    assert ast.literal_eval(installed_init.body[0].value)['version']==(3,3,34), '3.3.35 pre-install profile requires the 3.3.34 baseline'
unchanged={};changed={};added={};source_functions={};installed_functions={}
modules={'environment_tools/camera_views.py'}|set(profile['changed'])
for relative in sorted(modules):
    old=functions(args.installed/relative);new=functions(args.source/relative)
    installed_functions[relative]=old;source_functions[relative]=new
    removed=set(old)-set(new)
    actual_changed={name for name in set(old)&set(new) if dump(old[name])!=dump(new[name])}
    actual_added=set(new)-set(old)
    intended=set() if args.installed_current else profile['changed'].get(relative,set())
    assert not removed, 'Existing functions removed from '+relative+': '+str(sorted(removed))
    assert actual_changed==intended, 'Unexpected or missing function AST changes in '+relative+': '+str(sorted(actual_changed))
    if args.profile=='3.3.35':
        expected_added=set() if args.installed_current else profile['added'].get(relative,set())
        assert actual_added==expected_added, 'Unexpected or missing new helper functions in '+relative+': '+str(sorted(actual_added))
        for name in actual_changed:
            assert dump(old[name].args)==dump(new[name].args), 'Existing public signature changed: '+name
            assert [dump(item) for item in old[name].decorator_list]==[dump(item) for item in new[name].decorator_list], 'Existing decorators changed: '+name
    unchanged[relative]=sorted(set(old)-actual_changed)
    changed[relative]=sorted(actual_changed);added[relative]=sorted(actual_added)

if args.profile=='3.3.35':
    camera=source_functions['environment_tools/camera_views.py']
    assert set(profile['added']['environment_tools/camera_views.py'])<=set(camera)
    assert_calls(camera['capture_views'],('_physical_diffuse_state','_capture_views','sha'))
    assert_calls(camera['_capture_views'],('native.diffuse_passes','native.read_diffuse_passes',
        'native.compose_diffuse','native._save_png_from_linear','write_exr'))
    assert_calls(camera['physical_diffuse_main'],('load_config','capture_views','save_json'))
    assert_calls(camera['_physical_diffuse_state'],('image_format_state','restore_image_format'))
    for name in ('validate_camera_set','_capture_modes','capture_views','_capture_views','physical_diffuse_main'):
        assert any(isinstance(item,ast.Constant) and item.value=='physical-diffuse' for item in ast.walk(camera[name])), 'Explicit diffuse opt-in absent: '+name
    assert [dump(item) for item in camera['_physical_diffuse_state'].decorator_list]==[dump(ast.Name(id='contextmanager',ctx=ast.Load()))]
    assert dump(camera['_capture_views'].args)==dump(camera['capture_views'].args), 'Internal capture must preserve the existing public call contract'
    if not args.installed_current:
        # CLI main changes by this one rejection only; every other statement is
        # identical to 3.3.34, including the legacy bake/relink lifecycle.
        route_guard="""if view_entries and any(entry.get('mode', c['capture']['views'].get('mode', 'baked')) == 'physical-diffuse' for entry in view_entries):
    raise ValueError('Use installed environment_tools/camera_views.py --physical-diffuse; immutable physical references never relink or run the bake chain')"""
        new_main=copy.deepcopy(source_functions['environment_tools/cli.py']['main'])
        remove_exact_statements(new_main,[route_guard])
        assert dump(new_main)==dump(installed_functions['environment_tools/cli.py']['main']), 'CLI main changed beyond the explicit native-reference route guard'
        # Native diffuse rendering only adds sequencer/layer-denoise isolation;
        # the pass algorithm and existing restoration remain byte-for-byte AST.
        new_passes=copy.deepcopy(source_functions['workflow_tools/native_view_diagnostics.py']['diffuse_passes'])
        remove_exact_statements(new_passes,[
            "sequencer=getattr(scene.render,'use_sequencer',None)",
            "layer_cycles=getattr(view_layer,'cycles',None)",
            "layer_denoising=getattr(layer_cycles,'use_denoising',None)",
            'if sequencer is not None:scene.render.use_sequencer=False',
            'if layer_denoising is not None:layer_cycles.use_denoising=False',
            'if sequencer is not None:scene.render.use_sequencer=sequencer',
            'if layer_denoising is not None:layer_cycles.use_denoising=layer_denoising'])
        removed_receipt_keys=[]
        for item in ast.walk(new_passes):
            if isinstance(item,ast.Yield) and isinstance(item.value,ast.Dict):
                pairs=[]
                for key,value in zip(item.value.keys,item.value.values):
                    if isinstance(key,ast.Constant) and key.value in ('sequencer_used','layer_denoise'):
                        assert isinstance(value,ast.Constant) and value.value is False
                        removed_receipt_keys.append(key.value)
                    else:pairs.append((key,value))
                item.value.keys=[key for key,value in pairs];item.value.values=[value for key,value in pairs]
        assert sorted(removed_receipt_keys)==['layer_denoise','sequencer_used']
        assert dump(new_passes)==dump(installed_functions['workflow_tools/native_view_diagnostics.py']['diffuse_passes']), 'Native diffuse primitive changed beyond enumerated isolation/restoration'
with zipfile.ZipFile(args.archive) as archive:
    names=archive.namelist()
    assert len(names)==len(set(names)), 'Duplicate archive members'
    assert all(name.startswith('babylon_js/') and '..' not in Path(name).parts for name in names)
    packaged={name[len('babylon_js/'):]:sha(archive.read(name)) for name in names if not name.endswith('/')}
assert packaged==source, 'Built add-on differs from verified release-owned source files'
assert not any(name.endswith(('runtime.local.json','setup.local.log')) or '/node_modules/' in name for name in packaged)
record={'status':'passed','profile':args.profile,'release_file_count':len(source),'allowed_changes':differences,
        'unchanged_existing_camera_functions':unchanged['environment_tools/camera_views.py'],
        'unchanged_existing_functions':unchanged,'verified_changed_existing_functions':changed,'new_helper_functions':added,
        'preserve_local_state':local,'archive_sha256':sha(args.archive.read_bytes()),
        'limits':'Read-only pre-install inventory; installer must preserve these local files and Blender preferences'}
args.receipt.parent.mkdir(parents=True,exist_ok=True)
args.receipt.write_text(json.dumps(record,indent=2),encoding='utf8')
print(json.dumps({'status':'passed','release_files':len(source),'intended_differences':len(differences),'local_files':len(local)}))
