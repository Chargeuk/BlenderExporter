"""Build an installable legacy add-on ZIP using only the Python standard library."""
import ast
import os
from pathlib import Path
import zipfile


# Operational helpers must be present in every delivered add-on, not repo tools.
REQUIRED_TOOLS = (
    'workflow_tools/__init__.py',
    'workflow_tools/compare_appearance.py',
    'workflow_tools/inspect_blender.py',
    'workflow_tools/uv_audit.py',
    'workflow_tools/decoded_delivery.py',
    'workflow_tools/lightmap_ownership_filter.py',
    'workflow_tools/native_view_diagnostics.py',
    'workflow_tools/surface_equivalence.py',
    'workflow_tools/uv_tint_filter.py',
    'env_converter_setup.py',
    'workflow_tools/env_converter/setup.py',
    'workflow_tools/env_converter/runtime.cjs',
    'workflow_tools/env_converter/inspect-babylon.cjs',
    'workflow_tools/env_converter/inspect-babylon-fixture.cjs',
    'workflow_tools/env_converter/render-response-fixture.cjs',
    'workflow_tools/env_converter/convert.cjs',
    'workflow_tools/env_converter/package.json',
    'workflow_tools/env_converter/package-lock.json',
    'environment_tools/camera_views.py',
    'environment_tools/solid_views.py',
    'environment_tools/delivery_views.py',
    'environment_tools/native_local_patch.py',
    'environment_tools/native_ownership.py',
    'environment_tools/native_recombine.py',
    'workflow_tools/inspect_environment.py',
    'workflow_tools/enhance_reference.py',
    'environment_tools/cli.py',
    'environment_tools/worker.py',
    'environment_tools/receiver_response.py',
    'environment_tools/receiver_response_capture.py',
    'environment_tools/receiver_bake.py',
    'environment_tools/physical_colour_adjustment.py',
    'environment_tools/recipe_cli.py',
    'environment_tools/volume_assembly.py',
    'workflow_tools/volume_audit.py',
    'environment_tools/volume_capture.py',
    'environment_tools/volume_relocation.py',
    'environment_lighting_volume.py',
    'uastc_alpha.py',
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
        # Prune installed dependencies before traversing: they are local setup state,
        # never release assets, even when building from an already configured add-on.
        for current, directories, filenames in os.walk(source):
            directories[:] = sorted(name for name in directories
                                     if name.lower() not in ('node_modules', '__pycache__', '.git'))
            for name in sorted(filenames):
                file = Path(current)/name
                if (file.suffix.lower() not in ('.pyc', '.pyo', '.md')
                        and name.lower() not in ('runtime.local.json', 'setup.local.log')):
                    archive.write(file, file.relative_to(root / 'src').as_posix())
    return out


if __name__ == '__main__':
    print(build())
