"""Save an enhancement reference and reproducible receipt from Merserk's image API."""
import argparse
import base64
import hashlib
import io
import json
from pathlib import Path
import subprocess
import time
import uuid
import urllib.request

PRESET = dict(operation='neural', iterations=1, nr_passes=2, nr_style='Natural', nr_intensity=1.1,
    local_tone_strength=2., local_structure_strength=2., skin_structure_strength=-1.,
    automatic_mask=False, nr_color_strength=1., tone_preservation=.15,
    face_skin_protection=0., grain_preservation=.15, nr_gpu_mode=True)
ALLOWED = set(PRESET) | {'target_width','target_height','vsr_quality'}


def digest(data): return hashlib.sha256(data).hexdigest()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--url',default='http://192.168.1.1:7865')
    p.add_argument('--source',type=Path);p.add_argument('--output',type=Path)
    p.add_argument('--parameters',type=Path,help='JSON overrides of the Natural starting preset')
    p.add_argument('--tool-root',type=Path,default=Path('G:/ai/Merserk-DLSS5'))
    p.add_argument('--timeout',type=float,default=180)
    p.add_argument('--check',action='store_true')
    a=p.parse_args()
    if a.timeout<=0:raise ValueError('Timeout must be positive')
    url=a.url.rstrip('/')
    with urllib.request.urlopen(url+'/gradio_api/info',timeout=15) as response:
        info=json.load(response)
    endpoints=info.get('named_endpoints',{})
    required={'/vts_enhance_memory','/vts_cancel'}
    if not required.issubset(endpoints):raise ValueError('Required image API endpoints unavailable')
    if a.check:
        print(json.dumps({'status':'available','url':url,'endpoints':sorted(required)}));return
    if not a.source or not a.output:raise ValueError('--source and --output required')
    source=a.source.resolve();output=a.output.resolve();receipt=output.with_suffix('.json')
    if source==output or output.exists() or receipt.exists():raise ValueError('Use new output and receipt paths; originals are never overwritten')
    if output.suffix.lower()!='.png':raise ValueError('Output must be PNG')
    params=dict(PRESET)
    if a.parameters:
        overrides=json.loads(a.parameters.read_text(encoding='utf8'))
        if not isinstance(overrides,dict) or set(overrides)-ALLOWED:raise ValueError('Unsupported parameter; API does not accept custom masks or mask_feather')
        params.update(overrides)
    from PIL import Image
    from gradio_client import Client
    raw=source.read_bytes()
    with Image.open(io.BytesIO(raw)) as im:
        if im.format!='PNG' or im.mode not in ('RGB','RGBA'):raise ValueError('Use an 8-bit RGB/RGBA PNG screenshot, not an HDR master')
        source_size=list(im.size)
    version=None
    if (a.tool_root/'.git').exists():
        version=subprocess.run(['git','-C',str(a.tool_root),'rev-parse','HEAD'],capture_output=True,text=True,check=True).stdout.strip()
    client=Client(url,download_files=False,verbose=False,httpx_kwargs={'timeout':30.})
    request_id='codex-reference-'+uuid.uuid4().hex;started=time.monotonic()
    job=client.submit(base64.b64encode(raw).decode('ascii'),json.dumps(params),request_id,api_name='/vts_enhance_memory')
    try:
        while not job.done():
            if time.monotonic()-started>a.timeout:raise TimeoutError('Enhancement exceeded deadline')
            time.sleep(.5)
        result=job.result(timeout=1)
    except BaseException:
        # Cancel this helper's request only. Never stop the service or another user's job.
        try:client.submit(request_id,api_name='/vts_cancel').result(timeout=10)
        except Exception:pass
        raise
    decoded=base64.b64decode(result,validate=True)
    with Image.open(io.BytesIO(decoded)) as im:
        if im.format!='PNG':raise ValueError('Service returned a non-PNG result')
        output_size=list(im.size);im.verify()
    record={'schema_version':1,'url':url,'endpoint':'/vts_enhance_memory','request_id':request_id,
        'tool_commit':version,'api_info_sha256':digest(json.dumps(info,sort_keys=True).encode()),
        'source':str(source),'source_sha256':digest(raw),'source_dimensions':source_size,
        'output':str(output),'output_sha256':digest(decoded),'output_dimensions':output_size,
        'parameters':params,'elapsed_seconds':time.monotonic()-started,
        'purpose':'8-bit visual reference; not HDR lighting, material ground truth or geometry evidence'}
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('xb') as f:f.write(decoded)
    with receipt.open('x',encoding='utf8') as f:json.dump(record,f,indent=2)
    print(json.dumps(record,indent=2))


if __name__=='__main__':main()
