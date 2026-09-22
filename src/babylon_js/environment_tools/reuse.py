"""Explicit reuse planning and ownership checks, independent of Blender."""
import numpy as np


def load_labels(path):
    loaded=np.load(path,allow_pickle=False)
    if isinstance(loaded,np.lib.npyio.NpzFile):
        with loaded:
            if loaded.files!=['labels']:raise ValueError('Ownership archive requires only the labels array')
            return loaded['labels']
    return loaded


def reuse_plan(command, reuse, recombine, labels, catalogue):
    if recombine and not reuse:
        raise ValueError('--recombine requires --reuse-lightmaps')
    combining = reuse and (command == 'combine' or recombine)
    if reuse and command in ('bake', 'ownership', 'process'):
        raise ValueError('Cannot reuse masters for a rebake/processing command')
    if combining and command == 'preflight':
        raise ValueError('Choose combine or a later stage for recombination')
    if combining and (not labels or not catalogue):
        raise ValueError('Recombination requires --ownership-labels and --island-catalogue from the matching bake')
    if not combining and (labels or catalogue):
        raise ValueError('Ownership inputs are only used with lightmap recombination')
    names = ('direct', 'indirect', 'island_ids') + (() if combining else ('combined',))
    return combining, names


def validate_catalogue(saved_catalogue,current_catalogue):
    # Enumeration may change after sorting/reparenting; preserve saved IDs and
    # compare actual per-object chart triangles, never silently assign new IDs.
    def index(rows):
        result={};ids=set()
        for row in rows:
            key=(row['object'],row.get('polygon_root',0))
            if key in result or row['id'] in ids or not isinstance(row['id'],int) or row['id']<1:
                raise ValueError('Invalid duplicate chart identity')
            result[key]=row['triangles'];ids.add(row['id'])
        return result
    if index(saved_catalogue)!=index(current_catalogue):
        raise ValueError('Current receiver UV catalogue differs from the reused bake')


def validate_ownership(labels, saved_catalogue, current_catalogue, size):
    if labels.shape != (size, size) or not np.issubdtype(labels.dtype, np.integer):
        raise ValueError('Original ownership must be a matching square integer image')
    if (labels < 0).any() or not (labels > 0).any():
        raise ValueError('Invalid original ownership labels')
    validate_catalogue(saved_catalogue,current_catalogue)
    if not set(np.unique(labels)) - {0} <= {row['id'] for row in saved_catalogue}:
        raise ValueError('Ownership contains unknown island IDs')
    from ownership import rasterize
    raw = np.zeros((size, size, 4), np.float32)
    raw[:, :, 3] = labels > 0
    reconstructed, _ = rasterize(raw, saved_catalogue)
    if not np.array_equal(reconstructed, labels):
        raise ValueError('Original ownership disagrees with the current UV catalogue')
