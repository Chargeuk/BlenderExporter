import hashlib
import json
from pathlib import Path


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''): h.update(block)
    return h.hexdigest()


def save_json(path, value):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, indent=2), encoding='utf8')
    tmp.replace(path)


def load_config(path):
    path = Path(path).resolve()
    c = json.loads(path.read_text(encoding='utf8'))
    if c.get('schema_version') != 1: raise ValueError('Expected environment schema_version 1')
    c['_root'] = str(path.parent)
    c['_config'] = str(path)
    c['_source'] = str((path.parent / c['blend']).resolve())
    if not Path(c['_source']).is_file(): raise ValueError('Saved source .blend is missing')
    size = c.get('size', 2048)
    if not isinstance(size, int) or size < 16 or size > 8192: raise ValueError('Invalid square atlas size')
    c['size'] = size
    if c.get('quality', 'final') not in ('preview', 'final'): raise ValueError('quality must be preview or final')
    c.setdefault('samples', {'DIRECT': 128, 'INDIRECT': 2048})
    if any(not isinstance(c['samples'].get(p), int) or c['samples'][p] < 1 for p in ('DIRECT', 'INDIRECT')):
        raise ValueError('Positive DIRECT and INDIRECT sample counts are required')
    if c.get('quality', 'final') == 'final' and c['samples']['INDIRECT'] < 2048:
        raise ValueError('Final indirect bake requires at least 2048 samples; label cheaper work preview')
    if not 0 <= c.get('metallic_cap', .8) <= 1: raise ValueError('Invalid receiving metallic cap')
    for key in ('direct', 'indirect', 'island_ids', 'combined', 'colour'):
        if key not in c.get('images', {}): raise ValueError('Missing image binding: ' + key)
    return c
