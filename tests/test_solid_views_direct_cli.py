"""Actual direct-file Blender CLI regression; no bpy/solid_views import in driver.

Creates a saved disposable fixture, then launches independent serialized Blender
processes from an unrelated cwd with no PYTHONPATH. Old bytes must reproduce the
import failure; installed fixed bytes must render and preserve the CLI guards.
"""
import argparse,hashlib,json,os,shutil,subprocess,sys
from pathlib import Path

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--blender',required=True);parser.add_argument('--helper',required=True);parser.add_argument('--baseline-helper',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args();out=Path(args.output).resolve();assert not out.exists();out.mkdir(parents=True)
    blender=Path(args.blender).resolve();helper=Path(args.helper).resolve();baseline=Path(args.baseline_helper).resolve()
    sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
    old_hash='54b1738d1eb08bd049cbcc60074c700c08c9234ca5c36f6429582e8538271156'
    assert sha(baseline)==old_hash and sha(helper)!=old_hash
    cwd=out/'unrelated-cwd';cwd.mkdir()
    env=dict(os.environ);env.pop('PYTHONPATH',None);env.pop('PYTHONSTARTUP',None)
    steps=[]
    def run(label,argv,expected=0):
        stdout=out/(label+'.stdout.log');stderr=out/(label+'.stderr.log');assert not stdout.exists() and not stderr.exists()
        with stdout.open('xb') as a,stderr.open('xb') as b:
            p=subprocess.run([str(blender)]+[str(v) for v in argv],cwd=str(cwd),env=env,stdout=a,stderr=b,timeout=180,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        steps.append(dict(label=label,argv=[str(blender)]+[str(v) for v in argv],exit_code=p.returncode,stdout=str(stdout),stderr=str(stderr)))
        assert p.returncode==expected,(label,p.returncode,'See preserved stdout/stderr')
        return stdout.read_text(errors='replace')+'\n'+stderr.read_text(errors='replace')
    fixture=out/'fixture'
    run('prepare', ['--background','--factory-startup','--python-exit-code','1','--python',Path(__file__).with_name('solid_views_cli_fixture.py'),'--','--output',fixture])
    receipt=json.loads((fixture/'fixture-receipt.json').read_text());source=Path(receipt['source']);config=Path(receipt['config'])
    assert sha(source)==receipt['source_sha256'] and sha(config)==receipt['config_sha256']
    # Include real sibling dependencies next to old helper so failure is caused
    # by the missing bootstrap, not absent sibling files in the baseline folder.
    olddir=out/'baseline-helper';olddir.mkdir();old=olddir/'solid_views.py';shutil.copyfile(baseline,old)
    for name in ('camera_views.py','common.py'):shutil.copyfile(helper.with_name(name),olddir/name)
    def command(script,recipe,destination):
        return ['--background','--factory-startup',source,'--python-exit-code','1','--python',script,'--','--config',recipe,'--output',destination]
    oldout=out/'baseline-unexpected'
    text=run('baseline-direct-file',command(old,config,oldout),expected=1)
    assert "ModuleNotFoundError: No module named 'camera_views'" in text and not oldout.exists()
    capture=out/'installed-capture';run('installed-direct-file',command(helper,config,capture))
    result=json.loads((capture/'result.json').read_text())
    assert result['helper_sha256']==sha(helper) and result['camera_helper_sha256']==sha(helper.with_name('camera_views.py'))
    assert result['source_saved'] is False and result['source_sha256']==receipt['source_sha256'] and result['config_sha256']==receipt['config_sha256']
    assert result['delivery_triangles']==14 and len(result['delivery_membership'])==2 and len(result['views'])==2
    first,second=result['views']
    assert first['id']=='cli_complete' and first['full_delivery'] and first['displayed_triangles']==14 and first['backface_culling'] is False
    assert second['displayed_triangles']==12 and second['backface_culling'] is True
    assert second['omitted_faces']==[dict(source_object='CLI fixture box',evaluated_polygon_ids=[0],triangle_count=2)]
    for view in result['views']:
        assert {v['source_object'] for v in view['displayed']}=={'CLI fixture box','CLI fixture floor'}
        image=capture/view['file'];assert image.is_file() and sha(image)==view['sha256'] and view['dimensions']==[320,240]
    capture_hashes={p.name:sha(p) for p in capture.iterdir() if p.is_file()}
    text=run('fresh-output-rejected',command(helper,config,capture),expected=1)
    assert 'Fresh output directory required' in text and capture_hashes=={p.name:sha(p) for p in capture.iterdir() if p.is_file()}
    bad=json.loads(config.read_text());bad['source_sha256']='0'*64;bad_config=out/'bad-hash.json';bad_config.write_text(json.dumps(bad),encoding='utf-8')
    badout=out/'wrong-hash-unexpected';text=run('wrong-hash-rejected',command(helper,bad_config,badout),expected=1)
    assert 'Loaded source filename/hash differs from recipe' in text and not badout.exists()
    assert sha(source)==receipt['source_sha256'] and sha(config)==receipt['config_sha256'] and sha(baseline)==old_hash
    report=dict(status='passed',kind='Actual independent-process direct-file CLI regression',blender=str(blender),helper=str(helper),helper_sha256=sha(helper),baseline_sha256=old_hash,
        baseline_reproduced_missing_camera_views=True,rendered_views=2,negative_cli_guards=2,source_unchanged=True,config_unchanged=True,
        fixture_only=True,source019_opened=False,path_injection_in_driver=False,pythonpath_removed=True,steps=steps,
        limits='Workbench fixture/CLI proof only; no live reload, commissioned scene, PBR, export or KaDshow runtime acceptance')
    (out/'direct-cli-regression.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print('SOLID_VIEWS_DIRECT_FILE_CLI_PASSED: old failure reproduced; 2 installed renders; 2 CLI negative guards; immutable fixture/config')

if __name__=='__main__':main()
