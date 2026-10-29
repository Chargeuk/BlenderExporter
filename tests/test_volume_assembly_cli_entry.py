"""Actual bare CLI bootstrap regression; reaches strict source guard, no export."""
import argparse,hashlib,json,os,subprocess
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--blender',required=True);p.add_argument('--addon',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
a=p.parse_args();assert not a.output.exists();a.output.mkdir(parents=True)
helper=a.addon/'environment_tools/volume_assembly.py';config=a.output/'config.json'
config.write_text(json.dumps({'package_root':str(a.output),'source':'not-loaded.blend','source_sha256':'0'*64,'volume_export_route':'separate-source-v1'}))
source=config.read_bytes();unexpected=a.output/'unexpected'
argv=[a.blender,'--background','--factory-startup','--disable-autoexec','--python-exit-code','1','--python-expr',f'import sys;sys.path.insert(0,{str(a.addon.parent)!r});import babylon_js;babylon_js.register()','--python',str(helper),'--','--config',str(config),'--stage-a',str(a.output/'unused-stage-a'),'--output',str(unexpected)]
flags={'creationflags':subprocess.CREATE_NO_WINDOW}if os.name=='nt'else{}
proc=subprocess.run(argv,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False,**flags)
(a.output/'stdout.log').write_bytes(proc.stdout);(a.output/'stderr.log').write_bytes(proc.stderr)
text=(proc.stdout+proc.stderr).decode('utf8',errors='replace')
passed=proc.returncode==1 and 'Open the exact untouched saved source for Stage B' in text and 'ModuleNotFoundError' not in text and not unexpected.exists() and config.read_bytes()==source
result={'status':'passed' if passed else 'failed','tests':1,'argv':argv,'returncode':proc.returncode,'helper_sha256':hashlib.sha256(helper.read_bytes()).hexdigest(),'source_guard_reached':'Open the exact untouched saved source for Stage B' in text,'output_absent':not unexpected.exists(),'source_saved':False,'rendered':False,'encoded':False}
(a.output/'result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result));raise SystemExit(0 if passed else 1)
