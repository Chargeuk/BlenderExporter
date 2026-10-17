"""Build an installable legacy add-on ZIP using only the Python standard library."""
import ast
from pathlib import Path
import zipfile


# Operational helpers must be present in every delivered add-on, not repo tools.
REQUIRED_TOOLS = (
    'workflow_tools/__init__.py',
    'workflow_tools/compare_appearance.py',
    'workflow_tools/inspect_blender.py',
    'workflow_tools/inspect_environment.py',
    'workflow_tools/enhance_reference.py',
    'environment_tools/cli.py',
    'environment_tools/worker.py',
    'environment_tools/recipe_cli.py',
    'environment_tools/volume_capture.py',
    'environment_lighting_volume.py',
    'environment_tools/uv_geometry.py',
    'environment_tools/uv_prepare.py',
    'lightmap_tools/cli.py',
    'lightmap_tools/process_lightmap.py',
    'ktx_conversion.py',
)


def build(root=None, output=None):
    root = Path(root) if root is not None else Path(__file__).resolve().parents[1]
    source = root / 'src/babylon_js'
    missing = [name for name in REQUIRED_TOOLS if not (source / name).is_file()]
    if missing:
        raise ValueError('Missing packaged workflow tools: ' + ', '.join(missing))
    info = ast.parse((source / '__init__.py').read_text(encoding='utf8')).body[0]
    version = '.'.join(map(str, ast.literal_eval(info.value)['version']))
    out = Path(output) if output is not None else root / ('dist/Blender2Babylon-' + version + '-blender52.zip')
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(source.rglob('*')):
            # Codex skills own Markdown instructions; caches are never runtime assets.
            if (file.is_file() and '__pycache__' not in file.parts
                    and file.suffix.lower() not in ('.pyc', '.pyo', '.md')):
                archive.write(file, file.relative_to(root / 'src').as_posix())
    return out


if __name__ == '__main__':
    print(build())
