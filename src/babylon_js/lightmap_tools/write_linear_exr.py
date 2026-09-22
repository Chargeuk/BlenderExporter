"""Run with a Python interpreter containing NumPy and OpenImageIO."""
import argparse
from pathlib import Path
import numpy as np
import OpenImageIO as oiio


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('arrays', nargs='+', type=Path)
    args = p.parse_args()
    targets = [x.with_suffix('.exr') for x in args.arrays]
    if any(x.exists() for x in targets):
        raise FileExistsError('Refusing to overwrite an EXR')
    for source, target in zip(args.arrays, targets):
        a = np.load(source, allow_pickle=False)
        if a.dtype != np.float32 or a.ndim != 3 or a.shape[2] != 4 or not np.isfinite(a).all():
            raise ValueError('Expected finite float32 RGBA')
        h, w = a.shape[:2]
        spec = oiio.ImageSpec(w, h, 4, oiio.FLOAT)
        spec.channelnames = ['R', 'G', 'B', 'A']
        spec.alpha_channel = 3
        spec.attribute('colorInteropID', 'lin_rec709_scene')
        spec.attribute('oiio:ColorSpace', 'lin_rec709_scene')
        spec.attribute('compression', 'zip')
        image = oiio.ImageBuf(spec)
        image.set_pixels(oiio.ROI.All, np.ascontiguousarray(a))
        if not image.write(str(target)):
            raise RuntimeError(image.geterror())
        loaded = oiio.ImageBuf(str(target))
        pixels = loaded.get_pixels(oiio.FLOAT)
        if not np.array_equal(pixels, a):
            raise RuntimeError('EXR round-trip changed pixels')
        if loaded.spec().get_string_attribute('colorInteropID') != 'lin_rec709_scene':
            raise RuntimeError('EXR lost linear colour metadata')
        print(f'EXR round-trip verified: {target.name}', flush=True)


if __name__ == '__main__':
    main()
