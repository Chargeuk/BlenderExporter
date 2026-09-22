"""Shared offline lightmap processing, independent of a running Blender session."""
import argparse
import ast
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys


def addon_version():
    source = Path(__file__).resolve().parents[1] / '__init__.py'
    tree = ast.parse(source.read_text(encoding='utf8'))
    info = next(n for n in tree.body if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == 'bl_info' for t in n.targets))
    return '.'.join(map(str, ast.literal_eval(info.value)['version']))


def dependencies(oidn_library, image_python=None, diagnostics=False):
    """Exercise dependencies before creating outputs; never install anything."""
    report = {'plugin_version': addon_version(), 'python': sys.executable,
              'status': 'passed', 'dependencies': {}, 'errors': []}
    for name in ('numpy', 'scipy') + (('PIL',) if diagnostics else ()):
        try:
            module = importlib.import_module(name)
            report['dependencies'][name] = getattr(module, '__version__', 'available')
        except (ImportError, OSError) as exc:
            report['errors'].append(f'{name}: {exc}. Select an interpreter with this dependency installed.')
    library = Path(oidn_library).expanduser().resolve()
    if not library.is_file():
        report['errors'].append('OIDN library not found: ' + str(library))
    elif 'numpy' in report['dependencies']:
        try:
            from oidn_rt import OIDN
            import numpy as np
            with OIDN(library, 64 * 64) as denoiser:
                probe = denoiser.run(np.zeros((64, 64, 3), np.float32))
                if not np.isfinite(probe).all():
                    raise RuntimeError('OIDN probe returned non-finite pixels')
                report['dependencies']['oidn'] = {'library': str(library),
                    'version': denoiser.version, 'device_type': denoiser.device_type}
        except (ImportError, OSError, RuntimeError, AttributeError, ValueError) as exc:
            report['errors'].append('Cannot initialise OIDN: ' + str(exc))
    if image_python:
        writer = Path(image_python).expanduser().resolve()
        if not writer.is_file():
            report['errors'].append('EXR Python interpreter not found: ' + str(writer))
        else:
            # Test the actual writer, not just imports: float EXR + metadata roundtrip.
            probe = ("import sys,tempfile,pathlib,numpy as np; "
                     "sys.path.insert(0,sys.argv[1]); import write_linear_exr; "
                     "tmp=tempfile.TemporaryDirectory(); p=pathlib.Path(tmp.name)/'probe.npy'; "
                     "np.save(p,np.ones((2,2,4),np.float32)); "
                     "sys.argv=['write_linear_exr',str(p)]; write_linear_exr.main(); tmp.cleanup()")
            try:
                proc = subprocess.run([str(writer), '-c', probe, str(Path(__file__).parent)],
                    capture_output=True, text=True, timeout=30,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                if proc.returncode:
                    raise RuntimeError((proc.stderr or proc.stdout).strip())
                report['dependencies']['exr_writer'] = {'python': str(writer), 'roundtrip': 'passed'}
            except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
                report['errors'].append('EXR writer check failed: ' + str(exc))
    else:
        report['exr_output'] = 'disabled; provide --image-python for EXR delivery'
    if report['errors']:
        report['status'] = 'failed'
    return report


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv == ['--version']:
        print('BlenderExporter ' + addon_version() + ' / lightmap processor 1.0')
        return 0
    if not argv or argv[0] in ('-h', '--help'):
        print('Usage: python cli.py {check|process} --oidn-library LIBRARY [options]\n'
              'check: validate NumPy/SciPy, OIDN and optional --image-python EXR writer.\n'
              'process: same checks, then isolated denoising and dilation.\n'
              'Run process --help for raw/labels/output options. No Blender scene is edited.')
        return 0
    command, args = argv[0], argv[1:]
    if command not in ('check', 'process'):
        print('Expected check or process; use --help.', file=sys.stderr)
        return 2
    parser = argparse.ArgumentParser(description=__doc__, add_help=(command == 'check'))
    parser.add_argument('--oidn-library', required=True)
    parser.add_argument('--image-python')
    parser.add_argument('--diagnostics', action='store_true')
    if command == 'process' and any(a in ('--help', '-h') for a in args):
        print('process requires --raw FLOAT32_RGBA_NPY --labels INTEGER_NPY --output NEW_DIRECTORY\n'
              '  --oidn-library LIBRARY --confirm-linear-zero-margin\n'
              'Optional: --catalogue JSON --image-python PYTHON --context 8 --diagnostics\n'
              'See LIGHTMAP_PROCESSING.md for the input contract. Output directories must be new.')
        return 0
    if command == 'check':
        options = parser.parse_args(args)
    else:
        options, _ = parser.parse_known_args(args)
    report = dependencies(options.oidn_library, options.image_python, options.diagnostics)
    print(json.dumps(report, indent=2), flush=True)
    if report['status'] != 'passed':
        return 2
    if command == 'process':
        from process_lightmap import main as process
        process(args)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
