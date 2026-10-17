"""Prepare an authored environment from its saved scene, without editing that source."""
import argparse
import json
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from common import load_config, save_json, sha
from reuse import reuse_plan, load_labels
from camera_views import validate_camera_set
from native_ownership import ownership_method

HERE = Path(__file__).resolve().parent
LIGHTMAP = HERE.parent / 'lightmap_tools'
STAGES = ['preflight', 'geometry', 'response-capture', 'bake', 'ownership', 'process', 'response', 'ao', 'combine', 'views', 'capture', 'env', 'thumbnail', 'snapshot', 'validate']


def requested_stages(command, view_entries, native_recombine=False):
    stages=list(STAGES) if command=='restore' else STAGES[:STAGES.index(command)+1]
    if not view_entries:stages=[stage for stage in stages if stage!='views']
    if native_recombine:
        if command in STAGES[:STAGES.index('combine')]:raise ValueError('Native recombine requires combine or later')
        stages=['native-recombine-check']+[stage for stage in stages if stage in STAGES[STAGES.index('combine'):]]
    return stages


def validate_pipeline_config(c):
    """Saved-preview diagnostics never enter native preparation/bake stages."""
    if 'saved_preview_diagnostic' in c:
        raise ValueError('Saved-preview diagnostics must use the direct environment_tools/camera_views.py saved-preview entrypoint in background Blender; do not use cli.py views')


def preparation_command(argv):
    """Preparation shares this entrypoint without requiring denoising tools."""
    from recipe_cli import main as recipe_main
    p=argparse.ArgumentParser(description='Prepare a disposable source/export from project policy')
    p.add_argument('command',choices=('prepare-physical','prepare-albedo','prepare-export','bind-candidate'))
    p.add_argument('--config',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--blender',type=Path)
    p.add_argument('--size',type=int,default=512)
    p.add_argument('--reuse-current',action='store_true')
    p.add_argument('--audit-only',action='store_true')
    p.add_argument('--build-candidate',type=Path)
    a=p.parse_args(argv)
    command=a.command.removeprefix('prepare-')
    extra=[]
    if command=='bind-candidate':
        if not a.build_candidate:p.error('bind-candidate requires --build-candidate')
        recipe_main(command,a.config,[str(a.output),'--build-candidate',str(a.build_candidate)])
        return
    if not a.blender:p.error('Preparation requires --blender')
    config=json.loads(a.config.read_text(encoding='utf-8-sig'))
    source=(a.config.parent/config['blend' if command in ('physical','albedo') else 'source']).resolve()
    if config.get('package_root'):source=Path(config['package_root'])/config['source']
    if not source.is_file():raise ValueError('Missing saved preparation source: '+str(source))
    if command=='physical':
        extra=['--size',str(a.size)]+(['--reuse-current'] if a.reuse_current else [])
    if command=='export' and a.audit_only:extra=['--audit-only']
    flags={'creationflags':subprocess.CREATE_NO_WINDOW} if sys.platform=='win32' else {}
    subprocess.run([str(a.blender),'-b',str(source),'--python-exit-code','1','--python',str(HERE/'recipe_cli.py'),
        '--',command,'--config',str(a.config.resolve()),str(a.output.resolve()),*extra],check=True,**flags)


def main():
    if len(sys.argv)>1 and sys.argv[1] in ('prepare-physical','prepare-albedo','prepare-export','bind-candidate'):
        preparation_command(sys.argv[1:]);return
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=STAGES + ['restore'])
    p.add_argument('--config', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--blender', type=Path, required=True)
    p.add_argument('--image-python', type=Path, required=True)
    p.add_argument('--oidn-library', type=Path, required=True)
    p.add_argument('--resume', action='store_true')
    p.add_argument('--reuse-lightmaps', action='store_true',help='Use configured existing masters for capture/thumbnail work, without rebaking')
    p.add_argument('--recombine', action='store_true', help='With reuse, evaluate saved controls before later stages; implicit for combine')
    p.add_argument('--ownership-labels', type=Path, help='Matching zero-margin labels.npy or lossless labels.npz (labels array)')
    p.add_argument('--island-catalogue', type=Path, help='Matching bake islands.json, checked against current receivers')
    p.add_argument('--geometry-reference', type=Path, help='Original bake geometry/result.json; required for AO-only reuse')
    p.add_argument('--rebake-ao', action='store_true', help='Regenerate only AO while reusing D/I; implicit for the ao command')
    p.add_argument('--env-converter',type=Path,help='Optional maintained Node convert.cjs; enables ENV conversion')
    p.add_argument('--recombine-native-from',type=Path,help='Immutable original native candidate; only finishing_overrides may differ')
    p.add_argument('--original-tool-source-root',type=Path,help='Preserved complete original add-on source plus final-source provenance')
    a = p.parse_args(); c = load_config(a.config); validate_pipeline_config(c); root = a.output.resolve()
    view_entries=validate_camera_set(c)
    if view_entries and c['capture']['views'].get('delivery_contract'):
        raise ValueError('Use installed workflow_tools/decoded_delivery.py render-views; delivery inspection never runs the bake chain')
    native_ownership=ownership_method(c)=='native-island-id'
    native_plan=None
    if a.recombine_native_from:
        if not a.original_tool_source_root or a.reuse_lightmaps or a.recombine or a.rebake_ao or a.command in STAGES[:STAGES.index('combine')]:
            raise ValueError('Native recombine requires original tool source, a combine-or-later command, and no legacy reuse/rebake flags')
        from native_recombine import validate_original
        native_plan=validate_original(c,a.recombine_native_from,a.original_tool_source_root)
    elif a.original_tool_source_root or c.get('finishing_overrides'):
        raise ValueError('Finishing overrides/original tool source require --recombine-native-from')
    if native_ownership and a.reuse_lightmaps:
        raise ValueError('native-island-id ownership requires a fresh candidate with its matching native receipt; --reuse-lightmaps/--recombine is not supported')
    if a.command=='views' and not view_entries:
        raise ValueError('views requires an opt-in capture.views.camera_set')
    response_enabled=c.get('receiver_response',{}).get('enabled',False)
    if a.command in ('response-capture','response') and not response_enabled:
        raise ValueError('Enable receiver_response schema_version 1 for response stages')
    if response_enabled and a.reuse_lightmaps:
        raise ValueError('receiver_response with --reuse-lightmaps is unsupported; measure current response with a fresh native bake')
    ao_enabled=c.get('ao',{}).get('enabled',False)
    rebake_ao=a.rebake_ao or a.command=='ao'
    if rebake_ao and not ao_enabled:raise ValueError('AO regeneration requires an enabled AO recipe')
    if a.command=='ao' and not ao_enabled:raise ValueError('Enable and configure AO in the recipe first')
    if a.reuse_lightmaps and ao_enabled and a.command!='geometry' and not a.geometry_reference:
        raise ValueError('AO reuse requires --geometry-reference from the original bake scene')
    recombine, reused_names = reuse_plan('combine' if a.command=='ao' else a.command, a.reuse_lightmaps, a.recombine,
                                        a.ownership_labels, a.island_catalogue)
    if a.reuse_lightmaps and ao_enabled and not rebake_ao:reused_names+=('ao',)
    if a.reuse_lightmaps and rebake_ao and a.command!='ao' and not recombine:
        raise ValueError('Regenerated AO requires --recombine before capture/export')
    source_root = Path(c['_root'])
    if root == source_root or source_root in root.parents:
        raise ValueError('Candidate output must be outside the source environment folder')
    inputs = {str(a.config.resolve()): sha(a.config), c['_source']: sha(c['_source'])}
    dependencies=[HERE.parent/name for name in ('environment_controls.py','env_export.py','lighting_controls.py','lightmap_grading.py','agx_lightmap.py')]
    if c.get('image_dependency_scope','all')=='participants':
        dependencies.append(HERE.parent/'workflow_tools/inspect_environment.py')
    for f in sorted(HERE.glob('*.py')) + sorted(LIGHTMAP.glob('*.py')) + dependencies:
        inputs[str(f)] = sha(f)
    for im in c.get('source_images', []):
        f = (source_root/im).resolve(); inputs[str(f)] = sha(f)
    if native_plan:inputs.update(native_plan['inputs'])
    inputs[str(a.oidn_library.resolve())] = sha(a.oidn_library)
    inputs['reuse_lightmaps']=a.reuse_lightmaps
    inputs['recombine']=recombine
    inputs['rebake_ao']=rebake_ao
    inputs['env_converter']=str(a.env_converter.resolve()) if a.env_converter else None
    if a.geometry_reference:inputs[str(a.geometry_reference.resolve())]=sha(a.geometry_reference)
    if a.env_converter:inputs[str(a.env_converter.resolve())]=sha(a.env_converter)
    if a.reuse_lightmaps:
        for name in reused_names:
            f=(source_root/c['images'][name]).resolve();inputs[str(f)]=sha(f)
    provenance=None
    needs_baked_capture=(a.command!='preflight' and c.get('capture',{}).get('mode','baked')=='baked')
    needs_baked_views=False
    if view_entries and (a.command=='restore' or STAGES.index(a.command)>=STAGES.index('views')):
        view_spec=c['capture']['views']
        needs_baked_views=any(entry.get('mode',view_spec.get('mode','baked'))=='baked' for entry in view_entries)
    needs_baked_capture=needs_baked_views if a.command=='views' else needs_baked_capture or needs_baked_views
    needs_baked_thumbnail=(a.command in ('thumbnail','snapshot','validate','restore') and c.get('thumbnail',{}).get('mode','baked')=='baked')
    if a.reuse_lightmaps and not recombine and (needs_baked_capture or needs_baked_thumbnail):
        provenance=(source_root/c.get('combined_provenance',c['images']['combined']+'.provenance.json')).resolve()
        if not provenance.is_file():
            raise ValueError('Missing combined provenance; first combine --reuse-lightmaps with matching ownership')
        inputs[str(provenance)]=sha(provenance)
    if recombine:
        for f in (a.ownership_labels, a.island_catalogue):inputs[str(f.resolve())]=sha(f)
    manifest_path = root/'build.json'
    if root.exists():
        if not a.resume: raise ValueError('Use a new output directory or --resume')
        manifest = json.loads(manifest_path.read_text())
        if manifest['inputs'] != inputs: raise ValueError('Inputs/tools changed; create a new candidate directory')
    else:
        root.mkdir(parents=True)
        manifest = {'schema_version':1, 'inputs':inputs, 'stages':{}, 'quality':c.get('quality','final'), 'accepted':False}
    save_json(manifest_path,manifest)
    for directory in ('sources','masters'): (root/directory).mkdir(exist_ok=True)
    if native_plan:
        from native_recombine import copy_verified
        copy_verified(native_plan,root)
    if provenance:shutil.copyfile(provenance,root/'masters/combined.provenance.json')
    for path in c.get('source_images',[]):
        destination=(root/'source-assets'/path).resolve()
        if root not in destination.parents:raise ValueError('Source asset path escapes candidate')
        destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source_root/path,destination)
    for item in c.get('downloads',[]):
        target = root/'sources'/Path(item['path']).name
        original = source_root/item['path']
        if not target.exists():
            partial = target.with_suffix(target.suffix+'.partial')
            if original.exists(): shutil.copyfile(original,partial)
            else:
                print('Downloading '+item['url'],flush=True)
                with urllib.request.urlopen(item['url'], timeout=90) as r, partial.open('wb') as f:
                    shutil.copyfileobj(r,f)
            if sha(partial) != item['sha256']: raise ValueError('Source download checksum mismatch')
            partial.replace(target)
        if sha(target) != item['sha256']: raise ValueError('Source checksum mismatch: '+str(target))

    def run(args, log):
        with log.open('w',encoding='utf8') as stream:
            subprocess.run(list(map(str,args)),stdout=stream,stderr=subprocess.STDOUT,check=True)

    def worker(command, directory):
        args=[a.blender,'--background','--factory-startup',c['_source'],'--python-exit-code','1',
             '--python',HERE/'worker.py','--',a.config.resolve(),command,root,directory]
        if command=='env':args.append(a.env_converter.resolve())
        run(args,directory/'blender.log')

    def exr(array, name, directory):
        import numpy as np
        path=directory/(name+'.npy');np.save(path,array)
        run([a.image_python,LIGHTMAP/'write_linear_exr.py',path],directory/(name+'-exr.log'))
        shutil.copyfile(path.with_suffix('.exr'),root/'masters'/(name+'.exr'))

    requested=requested_stages(a.command,view_entries,native_plan is not None)
    if not response_enabled:requested=[x for x in requested if x not in ('response-capture','response')]
    if not ao_enabled:requested=[x for x in requested if x!='ao']
    if not a.env_converter:
        if a.command=='env':raise ValueError('env requires --env-converter')
        requested=[x for x in requested if x!='env']
    if a.reuse_lightmaps:
        skipped=('bake','ownership','process') if recombine else ('bake','ownership','process','combine')
        requested=[x for x in requested if x not in skipped]
        if not rebake_ao:requested=[x for x in requested if x!='ao']
        for name,filename in [('direct','direct_final.exr'),('indirect','indirect_final.exr'),('island_ids','island_ids.exr'),('combined','combined.exr'),('ao','ao.exr')]:
            if name in reused_names:shutil.copyfile(source_root/c['images'][name],root/'masters'/filename)
        if recombine:
            reuse_dir=root/'reuse';reuse_dir.mkdir(exist_ok=True)
            import numpy as np
            np.save(reuse_dir/'labels.npy',load_labels(a.ownership_labels))
            shutil.copyfile(a.island_catalogue,reuse_dir/'islands.json')
            requested.insert(requested.index('preflight')+1,'reuse-check')
    for name in requested:
        completed=manifest['stages'].get(name)
        if completed:
            if any(not (root/f).is_file() or sha(root/f)!=digest for f,digest in completed['outputs'].items()):
                raise ValueError('Completed output changed: '+name)
            print('Verified completed '+name,flush=True);continue
        stage=root/name
        if stage.exists():
            # Only discard an incomplete, tool-owned stage immediately under this candidate root.
            if stage.resolve().parent!=root or stage.is_symlink():raise ValueError('Unsafe stage path')
            shutil.rmtree(stage)
        stage.mkdir();started=time.time(); print('Starting '+name,flush=True)
        save_json(root/'progress.json',{'stage':name,'status':'running','started':started})
        if name=='preflight' and not a.reuse_lightmaps:
            run([sys.executable,LIGHTMAP/'cli.py','check','--oidn-library',a.oidn_library,
                 '--image-python',a.image_python,'--diagnostics'],stage/'dependencies.log')
        if name in ('preflight','geometry','reuse-check','native-recombine-check','response-capture','bake','ao','combine','views','capture','env','thumbnail','snapshot'): worker(name,stage)
        if name=='geometry' and a.geometry_reference:
            original=json.loads(a.geometry_reference.read_text())
            current=json.loads((stage/'result.json').read_text())
            if original.get('schema_version')!=1 or original.get('geometry_sha256')!=current['geometry_sha256']:
                raise ValueError('Geometry, transforms, UVs, normals or AO blocker participation changed; do not reuse the old bake')
        if name=='reuse-check':
            import numpy as np
            from reuse import validate_ownership
            from ownership import dilate
            labels=np.load(root/'reuse/labels.npy',allow_pickle=False)
            catalogue=json.loads((root/'reuse/islands.json').read_text())
            validate_ownership(labels,catalogue,catalogue,c['size'])
            ids=np.ones((*labels.shape,4),np.float32);ids[:,:,:3]=labels[:,:,None]
            if not np.array_equal(dilate(ids,labels),np.load(stage/'island_ids.npy',allow_pickle=False)):
                raise ValueError('Reused ID image differs from original ownership')
            result=json.loads((stage/'result.json').read_text())
            result['original_ownership_matches']=True;save_json(stage/'result.json',result)
        if name=='ownership':
            import numpy as np
            from ownership import rasterize
            catalogue=json.loads((root/'bake/islands.json').read_text())
            raw=np.load(root/'bake/direct_raw.npy')
            if native_ownership:
                from native_ownership import load_native_ids,validate_native_ids
                from ownership import candidate_diagnostic
                image,_=load_native_ids(c,root,catalogue)
                labels,report=validate_native_ids(image,catalogue,raw,np.load(root/'bake/indirect_raw.npy'))
                report['native_receipt_sha256']=sha(root/'bake/native_ownership.provenance.json')
                report['conservative_candidates']=candidate_diagnostic(labels,catalogue)
            else:labels,report=rasterize(raw,catalogue)
            np.save(stage/'labels.npy',labels)
            patches=json.loads((root/'bake/metal_patches.json').read_text())
            report['metal_patches']=[]
            for mode in ('direct','indirect'):
                raw=np.load(root/'bake'/(mode+'_raw.npy'))
                if not np.array_equal(raw[:,:,3]>0,labels>0):raise ValueError('Direct and indirect coverage differs')
                for row in (x for x in patches if x['pass']==mode):
                    mask=np.isin(labels,[x['id'] for x in catalogue if x['object']==row['object']])
                    with np.load(root/'bake'/row['file']) as data:local=data['pixels']
                    if not (local[:,:,3][mask]==1).all():raise ValueError('Capped receiver coverage differs')
                    raw[mask]=local[mask]
                    report['metal_patches'].append(dict(row,owned_pixels=int(mask.sum())))
                np.save(stage/(mode+'_raw.npy'),raw)
            save_json(stage/'result.json',report)
        if name=='process':
            import numpy as np
            from ownership import dilate
            for mode in ('direct','indirect'):
                run([sys.executable,LIGHTMAP/'cli.py','process','--raw',root/'ownership'/(mode+'_raw.npy'),
                     '--labels',root/'ownership/labels.npy','--catalogue',root/'bake/islands.json',
                     '--output',stage/mode,'--oidn-library',a.oidn_library,'--image-python',a.image_python,
                     '--context','8','--confirm-linear-zero-margin','--diagnostics'],stage/(mode+'.log'))
                shutil.copyfile(stage/mode/'final.exr',root/'masters'/(mode+'_final.exr'))
            labels=np.load(root/'ownership/labels.npy')
            ids=np.ones((*labels.shape,4),np.float32);ids[:,:,:3]=labels[:,:,None]
            exr(dilate(ids,labels),'island_ids',stage)
        if name=='response':
            from receiver_response import derive
            derive(c,root,stage,exr)
        if name=='combine':
            import numpy as np
            from ownership import dilate
            labels_path=root/('reuse' if recombine else 'ownership')/'labels.npy'
            exr(dilate(np.load(stage/'evaluated.npy'),np.load(labels_path)),'combined',stage)
            record=json.loads((stage/'appearance.json').read_text())
            record.update(combined_sha256=sha(root/'masters/combined.exr'),source_sha256=sha(c['_source']),
                          ownership_sha256=sha(labels_path))
            if native_plan:
                record['native_recombine_provenance_sha256']=sha(root/'native_recombine.provenance.json')
                record['original_native_candidate']=native_plan['original_candidate']
                record['finishing_overrides']=c.get('finishing_overrides',{})
            if response_enabled:
                record['receiver_response_provenance_sha256']=sha(root/'response/result.json')
                record['direct_indirect_inputs']='derived measured receiver response; native transport masters preserved'
            save_json(root/'masters/combined.provenance.json',record)
        if name=='ao':
            import numpy as np
            from ownership import dilate
            from reuse import validate_ownership
            labels_path=root/('reuse' if a.reuse_lightmaps else 'ownership')/'labels.npy'
            labels=np.load(labels_path,allow_pickle=False);raw=np.load(stage/'ao_raw.npy',allow_pickle=False)
            catalogue=json.loads((stage/'islands.json').read_text())
            saved=json.loads((root/('reuse' if a.reuse_lightmaps else 'bake')/'islands.json').read_text())
            native_ids=None
            if native_ownership:
                from native_ownership import load_native_ids,validate_native_ids
                native_ids,_=load_native_ids(c,root,saved)
                validate_native_ids(native_ids,saved,raw)
            validate_ownership(labels,saved,catalogue,c['size'],native_ids=native_ids)
            if not np.array_equal(raw[:,:,3]>0,labels>0):raise ValueError('AO coverage differs from lighting ownership')
            raw[:,:,:3]=np.clip(raw[:,:,:3],0.,1.)
            exr(dilate(raw,labels),'ao',stage)
        if name=='validate':
            worker('validate',stage)
            for f,digest in inputs.items():
                if f in ('reuse_lightmaps','recombine','rebake_ao','env_converter'):continue
                if sha(f)!=digest:raise ValueError('Source/tool changed during build: '+f)
        outputs={str(f.relative_to(root)):sha(f) for f in stage.rglob('*') if f.is_file()}
        if name=='bake':
            for f in ('albedo_uv.exr','albedo_uv.exr.json'): outputs['masters/'+f]=sha(root/'masters'/f)
        if name=='process':
            for f in ('direct_final.exr','indirect_final.exr','island_ids.exr'): outputs['masters/'+f]=sha(root/'masters'/f)
        if name=='response':
            for mode in ('direct','indirect'):
                for variant in ('raw','processed'):
                    f=mode+'_response_'+variant+'.exr';outputs['masters/'+f]=sha(root/'masters'/f)
        if name=='combine':
            for f in ('combined.exr','combined.provenance.json'):outputs['masters/'+f]=sha(root/'masters'/f)
        if name=='ao':outputs['masters/ao.exr']=sha(root/'masters/ao.exr')
        manifest['stages'][name]={'seconds':time.time()-started,'outputs':outputs}
        save_json(manifest_path,manifest);print('Completed '+name,flush=True)
    for f,digest in inputs.items():
        if f not in ('reuse_lightmaps','recombine','rebake_ao','env_converter') and sha(f)!=digest:
            raise ValueError('Source/tool changed during build: '+f)
    save_json(root/'progress.json',{'stage':requested[-1],'status':'completed','accepted':False})
    print('Candidate ready: '+str(root)+'; inspect it before replacing accepted assets.',flush=True)


if __name__=='__main__':
    try:main()
    except Exception as error:
        if '--output' in sys.argv:
            root=Path(sys.argv[sys.argv.index('--output')+1]).resolve()
            progress=root/'progress.json'
            if progress.is_file() and (root/'build.json').is_file():
                status=json.loads(progress.read_text());status.update(status='failed',error=str(error));save_json(progress,status)
        raise
