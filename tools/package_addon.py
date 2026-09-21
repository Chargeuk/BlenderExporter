"""Build an installable legacy add-on ZIP using only the Python standard library."""
from pathlib import Path
import zipfile
root=Path(__file__).resolve().parents[1]
out=root/'dist/Blender2Babylon-3.3.3-blender52.zip'
out.parent.mkdir(exist_ok=True)
with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as archive:
    for file in sorted((root/'src/babylon_js').rglob('*')):
        if file.is_file() and '__pycache__' not in file.parts and file.suffix != '.pyc':
            archive.write(file,file.relative_to(root/'src').as_posix())
print(out)
