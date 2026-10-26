"""Real-file volume reader smoke check; run in installed Blender Python + OIIO.

References must be independently prepared float32 RGB EXR and uint8 RGBA PNG
NumPy arrays (for example verified OIIO ImageBuf and existing Pillow reads).
Unlike unit-test doubles this opens the supplied actual EXR and PNG files.
This is test infrastructure, not a scene/runtime workflow dependency.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import numpy as np


def array_sha(array):return hashlib.sha256(np.ascontiguousarray(array).tobytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--helper',type=Path,default=Path(__file__).resolve().parents[1]/'src/babylon_js/workflow_tools/decoded_delivery.py')
    for name in ('exr','png','reference-exr','reference-png','output'):parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--exr-sha256',required=True);parser.add_argument('--png-sha256',required=True)
    parser.add_argument('--exr-size',nargs=2,type=int,default=[2048,2048],metavar=('WIDTH','HEIGHT'))
    parser.add_argument('--expected-peak',type=float,default=26.3827)
    parser.add_argument('--require-alpha',action='append',type=int,required=True)
    args=parser.parse_args()
    if args.output.exists():raise ValueError('Smoke result must use a fresh output file')
    import OpenImageIO as oiio  # Mandatory: fallback is not this backend smoke.
    spec=importlib.util.spec_from_file_location('volume_backend_smoke',args.helper.resolve())
    delivery=importlib.util.module_from_spec(spec);spec.loader.exec_module(delivery)
    paths=[args.helper,args.exr,args.png,args.reference_exr,args.reference_png]
    before={str(path.resolve()):delivery.sha(path) for path in paths}
    if delivery.sha(args.exr)!=args.exr_sha256 or delivery.sha(args.png)!=args.png_sha256:
        raise ValueError('Real smoke image differs from the explicitly selected SHA256')
    actual_exr=delivery.volume_linear(args.exr);actual_png=delivery.volume_png_rgba(args.png)
    expected_exr=np.load(args.reference_exr,allow_pickle=False);expected_png=np.load(args.reference_png,allow_pickle=False)
    if actual_exr.shape!=(args.exr_size[1],args.exr_size[0],3) or actual_exr.dtype!=np.float32:
        raise ValueError('Actual HDR source dimensions/float32 channels differ')
    if expected_exr.dtype!=np.float32 or expected_png.dtype!=np.uint8:
        raise ValueError('Independent references must preserve float32 RGB and uint8 RGBA')
    if not np.isfinite(actual_exr).all() or actual_exr.max()<=1 or not np.isclose(actual_exr.max(),args.expected_peak,rtol=0,atol=.001):
        raise ValueError('Real nonzero HDR peak differs; zero/LDR fixture is insufficient')
    if not np.array_equal(actual_exr,expected_exr):raise ValueError('Actual named RGB EXR read differs from independent verified reference')
    if not np.array_equal(actual_png,expected_png):raise ValueError('Raw PNG/divisor bytes differ from independent Pillow reference')
    if any(not 0<=value<=255 for value in args.require_alpha):raise ValueError('Expected alpha must be a raw byte')
    samples=[]
    for alpha in args.require_alpha:
        positions=np.argwhere(actual_png[...,3]==alpha)
        if not len(positions):raise ValueError('Required real PNG divisor byte absent: '+str(alpha))
        y,x=map(int,positions[0]);samples.append(dict(x=x,y=y,rgba=actual_png[y,x].tolist()))
    y,x,_=np.unravel_index(np.argmax(actual_exr),actual_exr.shape)
    exr_samples=[dict(x=int(x),y=int(y),rgb=actual_exr[y,x].tolist()),dict(x=0,y=0,rgb=actual_exr[0,0].tolist())]
    for path,digest in before.items():
        if delivery.sha(path)!=digest:raise ValueError('Backend smoke input changed: '+path)
    result=dict(status='passed',kind='actual-volume-image-backend-smoke',inputs=before,
        interpreter=sys.executable,oiio_version=str(getattr(oiio,'VERSION_STRING','unknown')),
        exr=dict(shape=list(actual_exr.shape),dtype=str(actual_exr.dtype),peak=float(actual_exr.max()),
            array_sha256=array_sha(actual_exr),reference_array_sha256=array_sha(expected_exr),samples=exr_samples),
        png=dict(shape=list(actual_png.shape),dtype=str(actual_png.dtype),
            alpha_bytes=np.unique(actual_png[...,3]).tolist(),required_alpha=args.require_alpha,
            array_sha256=array_sha(actual_png),reference_array_sha256=array_sha(expected_png),samples=samples),
        limits='Real input/backend byte agreement only; no volume capture, KTX compression, GPU, or runtime acceptance.')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2,allow_nan=False),encoding='utf8')
    print(json.dumps(result,indent=2,allow_nan=False))


if __name__=='__main__':main()
