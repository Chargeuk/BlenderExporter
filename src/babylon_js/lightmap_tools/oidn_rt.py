"""Small OIDN buffer wrapper for the locally verified RT colour-only recipe."""
import ctypes as C
import os
from pathlib import Path
import numpy as np


class OIDN:
    def __init__(self, library, capacity_pixels):
        self.search = None
        self.d = self.ib = self.ob = self.f = None
        self.capacity = capacity_pixels * 12
        library = Path(library).resolve(strict=True)
        if os.name == 'nt':
            self.search = os.add_dll_directory(str(library.parent))
        self.lib = C.CDLL(str(library))
        p, s, i, c = C.c_void_p, C.c_size_t, C.c_int, C.c_char_p
        bindings = [
            ('NewDevice', p, [i]), ('CommitDevice', None, [p]),
            ('GetDeviceError', i, [p, C.POINTER(c)]), ('GetDeviceInt', i, [p, c]),
            ('NewBuffer', p, [p, s]), ('WriteBuffer', None, [p, s, s, p]),
            ('ReadBuffer', None, [p, s, s, p]), ('NewFilter', p, [p, c]),
            ('SetFilterBool', None, [p, c, C.c_bool]), ('SetFilterInt', None, [p, c, i]),
            ('SetFilterFloat', None, [p, c, C.c_float]),
            ('SetFilterImage', None, [p, c, p, i, s, s, s, s, s]),
            ('CommitFilter', None, [p]), ('ExecuteFilter', None, [p]),
            ('ReleaseFilter', None, [p]), ('ReleaseBuffer', None, [p]),
            ('ReleaseDevice', None, [p])]
        for name, result, args in bindings:
            f = getattr(self.lib, 'oidn' + name)
            f.restype, f.argtypes = result, args
            setattr(self, name, f)
        try:
            self.d = self.NewDevice(0)
            if not self.d:
                raise RuntimeError('OIDN could not create the default device')
            self.CommitDevice(self.d)
            self.check()
            self.version = self.GetDeviceInt(self.d, b'version')
            self.device_type = self.GetDeviceInt(self.d, b'type')
            self.check()
            self.ib = self.NewBuffer(self.d, self.capacity)
            self.ob = self.NewBuffer(self.d, self.capacity)
            self.check()
            if not self.ib or not self.ob:
                raise RuntimeError('OIDN buffer allocation failed')
            self.f = self.NewFilter(self.d, b'RT')
            self.check()
            if not self.f:
                raise RuntimeError('OIDN RT unavailable')
            self.SetFilterBool(self.f, b'hdr', True)
            self.SetFilterInt(self.f, b'quality', 6)
            self.SetFilterFloat(self.f, b'inputScale', 1.0)
            self.check()
        except Exception:
            self.close()
            raise

    def check(self):
        message = C.c_char_p()
        error = self.GetDeviceError(self.d, C.byref(message))
        if error:
            raise RuntimeError(f'OIDN {error}: {message.value!r}')

    def run(self, a):
        a = np.ascontiguousarray(a, dtype=np.float32)
        if a.ndim != 3 or a.shape[2] != 3 or not np.isfinite(a).all():
            raise ValueError('OIDN expects finite H x W x 3 floats')
        if a.nbytes > self.capacity:
            raise ValueError('OIDN input exceeds allocated capacity')
        h, w = a.shape[:2]
        out = np.empty_like(a)
        self.WriteBuffer(self.ib, 0, a.nbytes, C.c_void_p(a.ctypes.data))
        for key, buffer in [(b'color', self.ib), (b'output', self.ob)]:
            self.SetFilterImage(self.f, key, buffer, 3, w, h, 0, 0, 0)
        self.CommitFilter(self.f)
        self.check()
        self.ExecuteFilter(self.f)
        self.check()
        self.ReadBuffer(self.ob, 0, out.nbytes, C.c_void_p(out.ctypes.data))
        self.check()
        if not np.isfinite(out).all():
            raise RuntimeError('OIDN returned non-finite pixels')
        return out

    def close(self):
        for name, release in [('f', 'ReleaseFilter'), ('ib', 'ReleaseBuffer'),
                              ('ob', 'ReleaseBuffer'), ('d', 'ReleaseDevice')]:
            value = getattr(self, name, None)
            if value:
                getattr(self, release)(value)
                setattr(self, name, None)
        if self.search is not None:
            self.search.close()
            self.search = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
