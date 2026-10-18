"""Distribution checks that require no Blender session or enhancement service."""
import ast
import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / 'src/babylon_js'
spec = importlib.util.spec_from_file_location('package_addon', ROOT / 'tools/package_addon.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class WorkflowPackagingTests(unittest.TestCase):
    def test_zip_contains_complete_workflow_and_runtime_assets(self):
        with tempfile.TemporaryDirectory() as temp:
            archive = builder.build(ROOT, Path(temp) / 'addon.zip')
            with zipfile.ZipFile(archive) as package:
                for relative in builder.REQUIRED_TOOLS:
                    member = 'babylon_js/' + relative
                    self.assertEqual(package.read(member), (ADDON / relative).read_bytes())
                example = 'environment_tools/example.environment-build.json'
                self.assertEqual(package.read('babylon_js/' + example), (ADDON / example).read_bytes())
                self.assertFalse(any(Path(name).suffix.lower() in ('.md', '.pyc', '.pyo')
                                     or '__pycache__' in Path(name).parts
                                     for name in package.namelist()))

    def test_builder_excludes_manuals_and_caches_even_if_source_contains_them(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'src/babylon_js'
            shutil.copytree(ADDON, source)
            (source / 'USAGE.MD').write_text('Instructions belong in Codex skills.')
            cache = source / 'workflow_tools/__pycache__'
            cache.mkdir(exist_ok=True)
            (cache / 'helper.pyc').write_bytes(b'cached')
            (source / 'workflow_tools/helper.pyo').write_bytes(b'optimized')
            with zipfile.ZipFile(builder.build(root, root / 'addon.zip')) as package:
                names = package.namelist()
                self.assertNotIn('babylon_js/USAGE.MD', names)
                self.assertNotIn('babylon_js/workflow_tools/__pycache__/helper.pyc', names)
                self.assertNotIn('babylon_js/workflow_tools/helper.pyo', names)

    def test_local_env_dependencies_and_browser_config_never_enter_release(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root/'src/babylon_js'
            shutil.copytree(ADDON, source)
            converter = source/'workflow_tools/env_converter'
            installed = converter/'node_modules/babylonjs'
            installed.mkdir(parents=True)
            (installed/'babylon.js').write_text('local dependency must not ship')
            nested = source/'environment_tools/nested/node_modules/transitive'
            nested.mkdir(parents=True)
            (nested/'index.js').write_text('nested dependency must not ship')
            (converter/'runtime.local.json').write_text('{"browser":"machine-specific path"}')
            (converter/'setup.local.log').write_text('local install diagnostics')
            with zipfile.ZipFile(builder.build(root, root/'addon.zip')) as package:
                self.assertFalse(any('node_modules' in Path(name).parts for name in package.namelist()))
                for name in ('runtime.local.json', 'setup.local.log'):
                    self.assertNotIn('babylon_js/workflow_tools/env_converter/'+name, package.namelist())
                for name in ('setup.py', 'runtime.cjs'):
                    self.assertEqual(package.read('babylon_js/workflow_tools/env_converter/'+name),
                                     (converter/name).read_bytes())

    def test_incomplete_workflow_refused_before_output_creation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'src/babylon_js'
            shutil.copytree(ADDON, source)
            (source / 'workflow_tools/inspect_environment.py').unlink()
            output = root / 'addon.zip'
            with self.assertRaisesRegex(ValueError, 'inspect_environment.py'):
                builder.build(root, output)
            self.assertFalse(output.exists())

    def test_cpu_helpers_run_from_extracted_zip_without_parent_blender_import(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with zipfile.ZipFile(builder.build(ROOT, root / 'addon.zip')) as package:
                package.extractall(root / 'installed')
            for name in ('compare_appearance.py', 'enhance_reference.py', 'uv_audit.py',
                         'decoded_delivery.py', 'native_view_diagnostics.py', 'lightmap_ownership_filter.py'):
                helper = root / 'installed/babylon_js/workflow_tools' / name
                command = [sys.executable, '-B']
                # The enhancement CLI help must not need Pillow, Gradio or a service.
                if name == 'enhance_reference.py':
                    command.append('-S')
                help_args = ['validate', '--help'] if name == 'decoded_delivery.py' else ['--help']
                result = subprocess.run(command + [str(helper), *help_args], cwd=root,
                                        capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn('--output', result.stdout)
                self.assertNotIn('bpy', result.stderr)

    def test_scene_audit_modules_preserve_reusable_entry_points(self):
        expected = {
            'inspect_blender.py': {'inspect_blender', 'inspect_jobs', 'compare_uv_snapshots', 'snapshot_uv2'},
            'inspect_environment.py': {'inspect_environment'},
        }
        for name, functions in expected.items():
            tree = ast.parse((ADDON / 'workflow_tools' / name).read_text(encoding='utf8'))
            self.assertTrue(functions <= {node.name for node in tree.body
                                         if isinstance(node, ast.FunctionDef)})
            # Loading by file path in Blender does not rely on an enabled exporter.
            self.assertFalse(any(isinstance(node, ast.ImportFrom) and node.level
                                 for node in ast.walk(tree)))


if __name__ == '__main__':
    unittest.main()
