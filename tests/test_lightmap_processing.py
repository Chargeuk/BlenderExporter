"""Bounded regression checks for tooling changes, not a per-bake settings sweep."""
import argparse
import json
from pathlib import Path
import tempfile
import subprocess
import sys
TOOLS = Path(__file__).resolve().parents[1] / 'src/babylon_js/lightmap_tools'
sys.path.insert(0, str(TOOLS))
import numpy as np
from mirrored_island_padding import mirrored_indices
from process_lightmap import process, validate
from oidn_rt import OIDN


class Identity:
    def run(self, a):
        return a.copy()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--oidn-library', type=Path, required=True)
    p.add_argument('--fixtures', type=Path, default=Path(__file__).parent / 'lightmap_fixtures')
    args = p.parse_args()
    manifest = json.loads((args.fixtures / 'manifest.json').read_text(encoding='utf-8'))
    records = []
    fixtures = [np.load(args.fixtures / row['file'], allow_pickle=False) for row in manifest['fixtures']]
    capacity = max(a['mask'].size for a in fixtures)
    with OIDN(args.oidn_library, capacity) as denoiser:
        for row, fixture in zip(manifest['fixtures'], fixtures):
            mask, raw = fixture['mask'], fixture['raw']
            sy, sx, counts = mirrored_indices(mask)
            assert np.array_equal(sy, fixture['source_y'])
            assert np.array_equal(sx, fixture['source_x'])
            assert mask[sy, sx].all()
            inp = raw[sy, sx]
            assert np.array_equal(inp[mask], raw[mask])
            out = denoiser.run(inp)
            # Same-host regression. Portability changes require investigation,
            # not silently loosening this reference tolerance.
            error = float(np.max(np.abs(out - fixture['expected'])))
            assert error <= 2e-6, (row['file'], error)
            records.append({'fixture': row['file'], 'maximum_absolute_error': error, **counts})
        black = denoiser.run(np.zeros((24, 24, 3), np.float32))
        assert np.max(np.abs(black)) < .001
        constant = denoiser.run(np.full((64, 64, 3), .2, np.float32))
        # RT is not an identity transform, even on a constant patch. This
        # catches gross scale errors; real fixtures check exact regressions.
        assert abs(float(constant.mean()) - .2) < .001
        assert np.max(np.abs(constant - .2)) < .01
        try:
            denoiser.run(np.empty((capacity+1, 1, 3), np.float32))
        except ValueError:
            pass
        else:
            raise AssertionError('Oversized OIDN input accepted')

    # Distinct adjacent islands, black valid data, one-pixel strip and chart
    # touching the atlas edge. Identity isolates padding/copy-back from OIDN.
    labels = np.zeros((40, 40), np.int32)
    labels[0:12, 0:15] = 1000000
    labels[15:28, 3:12] = 7
    labels[15:28, 13:25] = 9
    labels[31:39, 33] = 50
    raw = np.full((40, 40, 4), 123, np.float32)
    raw[:, :, 3] = labels > 0
    for k, colour in [(1000000, .1), (7, 0), (9, 2), (50, .6)]:
        raw[labels == k, :3] = colour
    valid, _ = validate(raw, labels)
    clean, final, rows = process(raw, labels, Identity())
    assert np.array_equal(clean[valid], raw[valid])
    assert np.array_equal(final[valid], clean[valid])
    assert np.all(final[:, :, 3] == 1)
    assert np.all(clean[labels == 7, :3] == 0)
    assert rows[-1]['crop'][0:2] == [0, 0]
    assert np.isin(final[:, :, 0], [np.float32(x) for x in [0, .1, .6, 2]]).all()
    rectangle = np.zeros((12, 12), bool)
    rectangle[3:9, 3:9] = True
    sy, sx, _ = mirrored_indices(rectangle)
    assert (sy[5, 2], sx[5, 2]) == (5, 4)

    bad = []
    a = raw.copy(); a[1, 1, 0] = np.nan; bad.append((a, labels, None))
    a = raw.copy(); a[1, 1, 3] = .4; bad.append((a, labels, None))
    l = labels.copy(); l[1, 1] = 0; bad.append((raw, l, None))
    bad.append((raw.astype(np.float64), labels, None))
    bad.append((raw, labels.astype(float), None))
    bad.append((raw[:39], labels[:39], None))
    bad.append((raw, labels, [{'id': 7}]))
    bad.append((raw, labels, [{'id': 7}, {'id': 7}]))
    bad.append((np.zeros_like(raw), np.zeros_like(labels), None))
    for a, l, c in bad:
        try:
            validate(a, l, c)
        except ValueError:
            continue
        raise AssertionError('Invalid input accepted')
    with tempfile.TemporaryDirectory() as directory:
        sentinel = Path(directory) / 'keep.txt'
        sentinel.write_text('protected', encoding='utf-8')
        command = [sys.executable, str(TOOLS / 'process_lightmap.py'),
                   '--raw', 'unused.npy', '--labels', 'unused.npy', '--output', directory,
                   '--oidn-library', str(args.oidn_library), '--confirm-linear-zero-margin']
        result = subprocess.run(command, capture_output=True, text=True)
        assert result.returncode != 0 and 'Output must be a new directory' in result.stderr
        assert sentinel.read_text(encoding='utf-8') == 'protected'
    print(json.dumps({'status': 'passed', 'real_fixtures': records,
                      'synthetic': 'constant, black, neighbouring islands, thin strip, atlas edge, sparse IDs',
                      'invalid_input_cases': len(bad), 'output_overwrite_refused': True}, indent=2))


if __name__ == '__main__':
    main()
