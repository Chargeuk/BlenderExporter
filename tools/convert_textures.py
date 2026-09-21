"""Standalone entry point for the same conversion core used by the Blender add-on."""
import importlib.util
from pathlib import Path

path = Path(__file__).resolve().parents[1] / 'src/babylon_js/ktx_conversion.py'
spec = importlib.util.spec_from_file_location('ktx_conversion', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
if __name__ == '__main__':
    module.main()
