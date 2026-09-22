"""End-to-end CLI, EXR and dependency-failure checks; optional prior-tool comparison."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'src/babylon_js/lightmap_tools'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--oidn-library', required=True)
    parser.add_argument('--image-python', required=True)
    parser.add_argument('--baseline-tools', type=Path)
    args = parser.parse_args()
    command = [sys.executable, '-B', str(TOOLS / 'cli.py')]
    deps = ['--oidn-library', args.oidn_library, '--image-python', args.image_python]
    def run(cmd, success=True):
        result = subprocess.run(cmd, capture_output=True, text=True)
        assert (result.returncode == 0) == success, result.stdout + result.stderr
        return result
    run(command + ['--help'])
    run(command + ['process', '--help'])
    check = json.loads(run(command + ['check', *deps]).stdout)
    assert check['status'] == 'passed' and check['dependencies']['exr_writer']['roundtrip'] == 'passed'
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        # Distinct shadowed/lit islands, including true black and a narrow chart.
        labels = np.zeros((64, 64), np.int32)
        labels[0:25, 0:25] = 1; labels[28:55, 5:35] = 2; labels[5:50, 48:50] = 3
        raw = np.zeros((64, 64, 4), np.float32); raw[:, :, 3] = labels > 0
        raw[labels == 2, :3] = np.random.default_rng(42).random((int((labels == 2).sum()), 3)) * 2
        raw[labels == 3, :3] = .2
        rawpath, labelpath = directory/'raw.npy', directory/'labels.npy'
        np.save(rawpath, raw); np.save(labelpath, labels)
        inputs = ['--raw', str(rawpath), '--labels', str(labelpath), '--confirm-linear-zero-margin']
        output = directory/'new'
        run(command + ['process', *deps, *inputs, '--output', str(output)])
        report = json.loads((output/'report.json').read_text())
        assert report['status'] == 'candidate_processing_verified' and report['exr_roundtrip_exact']
        assert report['plugin_version'] == check['plugin_version']
        assert (output/'final.exr').is_file()
        final = np.load(output/'final.npy')
        interiors = np.load(output/'interiors.npy')
        assert np.array_equal(final[labels > 0], interiors[labels > 0])
        sentinel = output/'keep'; sentinel.write_text('protected')
        run(command + ['process', *deps, *inputs, '--output', str(output)], success=False)
        assert sentinel.read_text() == 'protected'
        # No dependency installs or output creation on a missing-library failure.
        absent = directory/'must-not-exist'
        run(command + ['process', '--oidn-library', str(directory/'absent.dll'),
                       *inputs, '--output', str(absent)], success=False)
        assert not absent.exists()
        # -S simulates an interpreter without NumPy/SciPy; help still works.
        bare = [sys.executable, '-B', '-S', str(TOOLS/'cli.py')]
        run(bare + ['--help'])
        missing = run(bare + ['check', '--oidn-library', args.oidn_library], success=False)
        assert json.loads(missing.stdout)['status'] == 'failed'
        matched = None
        if args.baseline_tools:
            old = directory/'baseline'
            run([sys.executable, '-B', str(args.baseline_tools/'process_lightmap.py'),
                 *deps, *inputs, '--output', str(old)])
            for name in ('interiors.npy', 'final.npy'):
                assert np.array_equal(np.load(old/name), np.load(output/name)), name
            # EXR headers may carry creation-time metadata; compare decoded radiance.
            for name in ('interiors.exr', 'final.exr'):
                comparison = ('import sys,numpy as np,OpenImageIO as oiio; '
                    'a,b=[oiio.ImageBuf(p) for p in sys.argv[1:]]; '
                    'assert np.array_equal(a.get_pixels(oiio.FLOAT),b.get_pixels(oiio.FLOAT)); '
                    'assert a.spec().get_string_attribute("colorInteropID") == '
                    'b.spec().get_string_attribute("colorInteropID") == "lin_rec709_scene"')
                run([args.image_python, '-c', comparison, str(old/name), str(output/name)])
            matched = True
    print(json.dumps({'status': 'passed', 'plugin_version': check['plugin_version'],
        'dependency_preflight': True, 'exr_roundtrip': True, 'existing_output_preserved': True,
        'missing_dependencies_rejected_without_outputs': True, 'baseline_arrays_and_decoded_exrs_identical': matched}))


if __name__ == '__main__':
    main()
