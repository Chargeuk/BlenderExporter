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

HERE = Path(__file__).resolve().parent
LIGHTMAP = HERE.parent / 'lightmap_tools'
STAGES = ['preflight', 'bake', 'ownership', 'process', 'combine', 'capture', 'env', 'thumbnail', 'snapshot', 'validate']


def main():
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
    p.add_argument('--env-converter',type=Path,help='Optional maintained Node convert.cjs; enables ENV conversion')
    a = p.parse_args(); c = load_config(a.config); root = a.output.resolve()
    recombine, reused_names = reuse_plan(a.command, a.reuse_lightmaps, a.recombine,
                                        a.ownership_labels, a.island_catalogue)
    source_root = Path(c['_root'])
    if root == source_root or source_root in root.parents:
        raise ValueError('Candidate output must be outside the source environment folder')
    inputs = {str(a.config.resolve()): sha(a.config), c['_source']: sha(c['_source'])}
    dependencies=[HERE.parent/name for name in ('environment_controls.py','env_export.py','lighting_controls.py')]
    for f in sorted(HERE.glob('*.py')) + sorted(LIGHTMAP.glob('*.py')) + dependencies:
        inputs[str(f)] = sha(f)
    for im in c.get('source_images', []):
        f = (source_root/im).resolve(); inputs[str(f)] = sha(f)
    inputs[str(a.oidn_library.resolve())] = sha(a.oidn_library)
    inputs['reuse_lightmaps']=a.reuse_lightmaps
    inputs['recombine']=recombine
    inputs['env_converter']=str(a.env_converter.resolve()) if a.env_converter else None
    if a.env_converter:inputs[str(a.env_converter.resolve())]=sha(a.env_converter)
    if a.reuse_lightmaps:
        for name in reused_names:
            f=(source_root/c['images'][name]).resolve();inputs[str(f)]=sha(f)
    provenance=None
    needs_baked_capture=(a.command!='preflight' and c.get('capture',{}).get('mode','baked')=='baked')
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

    requested=STAGES if a.command=='restore' else STAGES[:STAGES.index(a.command)+1]
    if not a.env_converter:
        if a.command=='env':raise ValueError('env requires --env-converter')
        requested=[x for x in requested if x!='env']
    if a.reuse_lightmaps:
        skipped=('bake','ownership','process') if recombine else ('bake','ownership','process','combine')
        requested=[x for x in requested if x not in skipped]
        for name,filename in [('direct','direct_final.exr'),('indirect','indirect_final.exr'),('island_ids','island_ids.exr'),('combined','combined.exr')]:
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
        if name in ('preflight','reuse-check','bake','combine','capture','env','thumbnail','snapshot'): worker(name,stage)
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
            raw=np.load(root/'bake/direct_raw.npy');labels,report=rasterize(raw,catalogue)
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
        if name=='combine':
            import numpy as np
            from ownership import dilate
            labels_path=root/('reuse' if recombine else 'ownership')/'labels.npy'
            exr(dilate(np.load(stage/'evaluated.npy'),np.load(labels_path)),'combined',stage)
            record=json.loads((stage/'appearance.json').read_text())
            record.update(combined_sha256=sha(root/'masters/combined.exr'),source_sha256=sha(c['_source']),
                          ownership_sha256=sha(labels_path))
            save_json(root/'masters/combined.provenance.json',record)
        if name=='validate':
            worker('validate',stage)
            for f,digest in inputs.items():
                if f in ('reuse_lightmaps','recombine','env_converter'):continue
                if sha(f)!=digest:raise ValueError('Source/tool changed during build: '+f)
        outputs={str(f.relative_to(root)):sha(f) for f in stage.rglob('*') if f.is_file()}
        if name=='process':
            for f in ('direct_final.exr','indirect_final.exr','island_ids.exr'): outputs['masters/'+f]=sha(root/'masters'/f)
        if name=='combine':
            for f in ('combined.exr','combined.provenance.json'):outputs['masters/'+f]=sha(root/'masters'/f)
        manifest['stages'][name]={'seconds':time.time()-started,'outputs':outputs}
        save_json(manifest_path,manifest);print('Completed '+name,flush=True)
    for f,digest in inputs.items():
        if f not in ('reuse_lightmaps','recombine','env_converter') and sha(f)!=digest:
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
