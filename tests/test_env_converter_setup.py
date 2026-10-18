"""Explicit setup must install only locked local modules and preserve browser choices."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT/'src/babylon_js'
SOURCE = ADDON/'workflow_tools/env_converter'
spec = importlib.util.spec_from_file_location('env_setup_core_tests', SOURCE/'setup.py')
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)/'selected converter with spaces'
        self.folder.mkdir()
        for name in ('setup.py', 'runtime.cjs', 'convert.cjs', 'package.json', 'package-lock.json'):
            shutil.copyfile(SOURCE/name, self.folder/name)
        self.converter = self.folder/'convert.cjs'
        self.node = Path(self.temp.name)/'Node runtime/node.exe'
        self.node.parent.mkdir()
        self.node.write_bytes(b'fixture executable')
        self.npm_cli = self.node.parent/'node_modules/npm/bin/npm-cli.js'
        self.npm_cli.parent.mkdir(parents=True)
        self.npm_cli.write_text('// fixture npm CLI')
        self.browser = Path(self.temp.name)/'Existing Chrome/chrome.exe'
        self.browser.parent.mkdir()
        self.browser.write_bytes(b'fixture browser')
        self.calls = []

    def fake_run(self, command, folder, log, timeout=60):
        args = list(map(str, command))
        self.calls.append((args, Path(folder), timeout))
        if args[-1] == '--version':
            return '10.9.4' if str(self.npm_cli) in args else 'v22.21.1'
        if 'ci' in args:
            return 'installed pinned packages'
        if '-e' in args:
            return str(self.browser)
        if 'install' in args:
            return 'download explicitly requested'
        if '--check' in args:
            selected = args[args.index('--browser')+1] if '--browser' in args else json.loads((self.folder/setup.CONFIG).read_text())['browser']
            if Path(selected) != self.browser:
                raise setup.SetupError('incompatible browser')
            return json.dumps({'status': 'ready', 'babylon': '7.27.0', 'playwright': '1.56.1',
                               'chromium': selected, 'webgl': 2})
        raise AssertionError(args)

    def install(self, **options):
        with patch.dict(os.environ, {'BJS_ENV_CHROMIUM': ''}), patch.object(setup, 'run', side_effect=self.fake_run):
            return setup.setup_converter(self.converter, node=self.node, npm_cli=self.npm_cli, **options)

    def test_windows_npm_uses_node_and_js_entrypoint_with_spaces(self):
        self.assertEqual(setup.npm_command(self.node), [self.node, self.npm_cli.resolve()])
        with patch.object(setup.shutil, 'which', return_value=None):
            missing = self.node.parent/'elsewhere/node.exe'
            with self.assertRaisesRegex(setup.SetupError, 'npm is required'):
                setup.npm_command(missing)

    def test_locked_install_and_persisted_readiness_target_selected_folder(self):
        original = {name: (self.folder/name).read_bytes() for name in ('package.json', 'package-lock.json')}
        with patch.object(setup, 'browser_candidates', return_value=[self.folder/'bad browser', self.browser]):
            result = self.install()
        self.assertEqual(result['converter'], str(self.converter))
        self.assertEqual(result['chromium'], str(self.browser))
        npm_calls = [args for args, _, _ in self.calls if 'ci' in args]
        self.assertEqual(npm_calls, [[str(self.node), str(self.npm_cli), 'ci', '--ignore-scripts', '--no-audit', '--no-fund']])
        self.assertTrue(all(folder == self.folder for _, folder, _ in self.calls))
        self.assertFalse(any('install' in args for args, _, _ in self.calls))
        self.assertNotIn('--browser', self.calls[-1][0])
        self.assertEqual(json.loads((self.folder/setup.CONFIG).read_text())['browser'], str(self.browser))
        self.assertEqual({name: (self.folder/name).read_bytes() for name in original}, original)

    def test_missing_node_and_unmanaged_override_do_not_install(self):
        with patch.object(setup.shutil, 'which', return_value=None), patch.object(setup, 'run') as run:
            with self.assertRaisesRegex(setup.SetupError, 'Node.js 18'):
                setup.setup_converter(self.converter)
            self.assertFalse(run.called)
        (self.folder/'runtime.cjs').unlink()
        with patch.object(setup, 'run') as run:
            with self.assertRaisesRegex(setup.SetupError, 'Selected converter has no supported setup companion'):
                setup.setup_converter(self.converter)
            self.assertFalse(run.called)

    def test_failed_install_and_browser_failure_preserve_existing_config(self):
        config = self.folder/setup.CONFIG
        original = b'{"schema_version":1,"browser":"preserve old selection"}'
        config.write_bytes(original)
        real_fake = self.fake_run
        def fail_ci(command, folder, log, timeout=60):
            if 'ci' in command:
                raise setup.SetupError('npm network failure')
            return real_fake(command, folder, log, timeout)
        with patch.object(setup, 'run', side_effect=fail_ci):
            with self.assertRaisesRegex(setup.SetupError, 'npm network failure'):
                setup.setup_converter(self.converter, node=self.node, npm_cli=self.npm_cli)
        self.assertEqual(config.read_bytes(), original)
        with patch.object(setup, 'browser_candidates', return_value=[]):
            with self.assertRaisesRegex(setup.SetupError, 'No compatible Chromium'):
                self.install()
        self.assertEqual(config.read_bytes(), original)
        self.assertFalse(any('install' in args for args, _, _ in self.calls))

    def test_browser_download_requires_explicit_opt_in_and_never_replaces_explicit_choice(self):
        with patch.object(setup, 'browser_candidates', return_value=[]):
            result = self.install(download_browser=True)
        downloads = [args for args, _, _ in self.calls if 'install' in args]
        self.assertEqual(downloads, [[str(self.node), str(self.folder/'node_modules/playwright-core/cli.js'), 'install', 'chromium']])
        self.assertEqual(result['chromium'], str(self.browser))
        self.calls.clear()
        with self.assertRaisesRegex(setup.SetupError, 'No compatible Chromium'):
            self.install(browser=self.folder/'missing requested browser', download_browser=True)
        self.assertFalse(any('install' in args for args, _, _ in self.calls))

    def test_persisted_probe_failure_rolls_back_config_and_check_only_never_writes(self):
        config = self.folder/setup.CONFIG
        original = b'old config bytes'
        config.write_bytes(original)
        real_fake = self.fake_run
        def fail_default(command, folder, log, timeout=60):
            if '--check' in command and '--browser' not in command:
                raise setup.SetupError('saved browser not honored')
            return real_fake(command, folder, log, timeout)
        with patch.object(setup, 'run', side_effect=fail_default):
            with self.assertRaisesRegex(setup.SetupError, 'saved browser not honored'):
                setup.setup_converter(self.converter, node=self.node, npm_cli=self.npm_cli, browser=self.browser)
        self.assertEqual(config.read_bytes(), original)
        (self.folder/setup.LOG).unlink()
        result = self.install(browser=self.browser, check_only=True)
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(config.read_bytes(), original)
        self.assertFalse((self.folder/setup.LOG).exists())

    def test_discovery_reuses_saved_cache_and_system_browser(self):
        (self.folder/setup.CONFIG).write_text(json.dumps({'browser': str(self.browser)}))
        system = Path(self.temp.name)/'Program Files/Google/Chrome/Application/chrome.exe'
        system.parent.mkdir(parents=True)
        system.write_bytes(b'fixture Chrome')
        with patch.dict(os.environ, {'PROGRAMFILES': str(Path(self.temp.name)/'Program Files')}, clear=True), \
             patch.object(setup.Path, 'home', return_value=Path(self.temp.name)/'Home'), \
             patch.object(setup.shutil, 'which', return_value=None):
            candidates = setup.browser_candidates(self.folder, str(self.browser))
        self.assertEqual(candidates, [self.browser, system])


package = types.ModuleType('env_converter_test_package')
package.__path__ = [str(ADDON)]
sys.modules[package.__name__] = package
from env_converter_test_package import env_export


class ProbeTests(unittest.TestCase):
    def test_explicit_setup_node_path_is_reused_without_global_path_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            script = Path(temp)/'convert.cjs'
            script.write_text('// fixture')
            node = Path(temp)/'Node runtime with spaces/node.exe'
            node.parent.mkdir()
            node.write_bytes(b'fixture Node')
            (script.parent/setup.CONFIG).write_text(json.dumps({'schema_version': 1, 'browser': 'saved browser', 'node': str(node)}))
            ready = types.SimpleNamespace(returncode=0, stdout=json.dumps({'status': 'ready', 'babylon': '7.27.0'}))
            env_export._probe.cache_clear()
            with patch.object(env_export, 'helper_path', return_value=script), \
                 patch.object(env_export.shutil, 'which', return_value=None), \
                 patch.object(env_export.subprocess, 'run', return_value=ready) as run:
                self.assertEqual(env_export.find_converter()[0], str(node))
                self.assertEqual(run.call_args.args[0][0], str(node))
            env_export._probe.cache_clear()

    def test_readiness_cache_tracks_runtime_config_and_missing_dependencies(self):
        with tempfile.TemporaryDirectory() as temp:
            script = Path(temp)/'convert.cjs'
            script.write_text('// fixture')
            ready = types.SimpleNamespace(returncode=0, stdout=json.dumps({'status': 'ready', 'babylon': '7.27.0'}))
            env_export._probe.cache_clear()
            with patch.object(env_export, 'helper_path', return_value=script), \
                 patch.object(env_export.shutil, 'which', return_value='node'), \
                 patch.object(env_export.subprocess, 'run', return_value=ready) as run:
                env_export.find_converter()
                env_export.find_converter()
                self.assertEqual(run.call_count, 1)
                (script.parent/setup.CONFIG).write_text('{"schema_version":1,"browser":"new selection"}')
                env_export.find_converter()
                self.assertEqual(run.call_count, 2)
                dependency = script.parent/'node_modules/babylonjs/package.json'
                dependency.parent.mkdir(parents=True)
                dependency.write_text('{"version":"7.27.0"}')
                env_export.find_converter()
                self.assertEqual(run.call_count, 3)
            env_export._probe.cache_clear()


class PreferencesSetupTests(unittest.TestCase):
    def load_operator(self):
        class Operator:
            def report(self, level, message):
                self.last_report = (level, message)
        bpy = types.SimpleNamespace(types=types.SimpleNamespace(Operator=Operator), path=types.SimpleNamespace(abspath=lambda value: value))
        spec = importlib.util.spec_from_file_location(package.__name__+'.env_converter_setup', ADDON/'env_converter_setup.py')
        ui = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'bpy': bpy}):
            spec.loader.exec_module(ui)
        self.addCleanup(lambda: setattr(ui.BJS_OT_env_converter_setup, '_running', False))
        return ui

    def test_modal_setup_stays_running_without_blocking_and_handles_success_and_failure(self):
        ui = self.load_operator()
        for exit_code in (0, 1):
            with self.subTest(exit_code=exit_code), tempfile.TemporaryDirectory() as temp:
                prefs = types.SimpleNamespace(env_converter_browser='existing browser with spaces',
                    env_converter_download_browser=False, env_converter_status='')
                wm = types.SimpleNamespace(event_timer_add=Mock(return_value=object()), modal_handler_add=Mock(), event_timer_remove=Mock())
                context = types.SimpleNamespace(window=object(), window_manager=wm,
                    preferences=types.SimpleNamespace(addons={'babylon_js': types.SimpleNamespace(preferences=prefs)}))
                process = types.SimpleNamespace(returncode=None)
                process.poll = lambda: process.returncode
                selected = Path(temp)/'selected converter/convert.cjs'
                operator = ui.BJS_OT_env_converter_setup()
                with patch.object(ui.env_export, 'helper_path', return_value=selected), \
                     patch.object(ui.converter_setup, 'managed_folder'), \
                     patch.object(ui, 'setup_python', return_value='verified Python'), \
                     patch.object(ui.subprocess, 'Popen', return_value=process) as spawn:
                    self.assertEqual(operator.execute(context), {'RUNNING_MODAL'})
                    self.assertFalse(ui.BJS_OT_env_converter_setup.poll(context))
                    command = spawn.call_args.args[0]
                    self.assertEqual(command[command.index('--converter')+1], str(selected))
                    self.assertNotIn('--download-browser', command)
                    event = types.SimpleNamespace(type='TIMER')
                    self.assertEqual(operator.modal(context, event), {'RUNNING_MODAL'})
                    log_path = Path(operator._log.name)
                    operator._log.write((json.dumps({'status': 'ready', 'chromium': 'selected browser'})+'\n').encode() if exit_code == 0 else b'fixture setup failure\n')
                    operator._log.flush()
                    process.returncode = exit_code
                    self.assertEqual(operator.modal(context, event), {'FINISHED'} if exit_code == 0 else {'CANCELLED'})
                    self.assertTrue(ui.BJS_OT_env_converter_setup.poll(context))
                    self.assertFalse(log_path.exists())
                    wm.event_timer_remove.assert_called_once()

    def start_operator(self, ui, temp):
        prefs = types.SimpleNamespace(env_converter_browser='',
            env_converter_download_browser=False, env_converter_status='')
        wm = types.SimpleNamespace(event_timer_add=Mock(return_value=object()),
            modal_handler_add=Mock(), event_timer_remove=Mock())
        context = types.SimpleNamespace(window=object(), window_manager=wm,
            preferences=types.SimpleNamespace(addons={'babylon_js': types.SimpleNamespace(preferences=prefs)}))
        process = types.SimpleNamespace(poll=Mock(return_value=None), kill=Mock(), wait=Mock())
        operator = ui.BJS_OT_env_converter_setup()
        with patch.object(ui.env_export, 'helper_path', return_value=Path(temp)/'convert.cjs'), \
             patch.object(ui.converter_setup, 'managed_folder'), \
             patch.object(ui, 'setup_python', return_value='verified Python'), \
             patch.object(ui.subprocess, 'Popen', return_value=process):
            self.assertEqual(operator.execute(context), {'RUNNING_MODAL'})
        return operator, context, process, Path(operator._log.name)

    def assert_cleaned(self, ui, operator, context, log_path):
        self.assertIsNone(ui._active)
        self.assertTrue(ui.BJS_OT_env_converter_setup.poll(context))
        self.assertIsNone(operator._process)
        self.assertIsNone(operator._log)
        self.assertFalse(log_path.exists())
        context.window_manager.event_timer_remove.assert_called_once()

    def test_missing_preferences_and_window_change_cancel_owned_work(self):
        for change in ('missing preferences', 'changed window'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temp:
                ui = self.load_operator()
                operator, context, process, log_path = self.start_operator(ui, temp)
                if change == 'missing preferences':
                    context.preferences.addons.clear()
                else:
                    context.window = None
                with patch.object(ui, 'stop_process') as stop:
                    self.assertEqual(operator.modal(context, types.SimpleNamespace(type='TIMER')), {'CANCELLED'})
                stop.assert_called_once_with(process)
                self.assert_cleaned(ui, operator, context, log_path)

    def test_cancel_escape_and_unregister_are_idempotent(self):
        for trigger in ('cancel', 'escape', 'unregister'):
            with self.subTest(trigger=trigger), tempfile.TemporaryDirectory() as temp:
                ui = self.load_operator()
                operator, context, process, log_path = self.start_operator(ui, temp)
                with patch.object(ui, 'stop_process') as stop:
                    if trigger == 'cancel':
                        operator.cancel(context)
                    elif trigger == 'escape':
                        self.assertEqual(operator.modal(context, types.SimpleNamespace(type='ESC')), {'CANCELLED'})
                    else:
                        ui.cancel_setup()
                    operator.cancel(context)
                    ui.cancel_setup()
                stop.assert_called_once_with(process)
                self.assert_cleaned(ui, operator, context, log_path)
                self.assertEqual(operator.modal(context, types.SimpleNamespace(type='TIMER')), {'CANCELLED'})

    def test_modal_error_is_preserved_when_cleanup_steps_raise(self):
        ui = self.load_operator()
        with tempfile.TemporaryDirectory() as temp:
            operator, context, process, log_path = self.start_operator(ui, temp)
            context.window_manager.event_timer_remove.side_effect = RuntimeError('timer already removed')
            with patch.object(ui, 'open', side_effect=OSError('original log read failure'), create=True), \
                 patch.object(ui, 'stop_process', side_effect=OSError('tree cleanup failure')):
                self.assertEqual(operator.modal(context, types.SimpleNamespace(type='TIMER')), {'CANCELLED'})
            self.assertIn('original log read failure', operator.last_report[1])
            self.assertIn('tree cleanup failure', operator._cleanup_errors)
            self.assertIn('timer already removed', operator._cleanup_errors)
            process.kill.assert_called_once()
            process.wait.assert_called_once_with(timeout=5)
            self.assert_cleaned(ui, operator, context, log_path)

    def test_startup_failure_after_spawn_cleans_owned_process_and_log(self):
        ui = self.load_operator()
        prefs = types.SimpleNamespace(env_converter_browser='', env_converter_download_browser=False)
        process = types.SimpleNamespace(poll=Mock(return_value=None))
        context = types.SimpleNamespace(window=object(),
            window_manager=types.SimpleNamespace(event_timer_add=Mock(side_effect=RuntimeError('window lost'))),
            preferences=types.SimpleNamespace(addons={'babylon_js': types.SimpleNamespace(preferences=prefs)}))
        operator = ui.BJS_OT_env_converter_setup()
        created_logs = []
        real_temporary = tempfile.NamedTemporaryFile
        def create_log(*args, **kwargs):
            log = real_temporary(*args, **kwargs)
            created_logs.append(log)
            return log
        with patch.object(ui.env_export, 'helper_path', return_value=SOURCE/'convert.cjs'), \
             patch.object(ui.converter_setup, 'managed_folder'), \
             patch.object(ui, 'setup_python', return_value='verified Python'), \
             patch.object(ui.subprocess, 'Popen', return_value=process), \
             patch.object(ui, 'stop_process') as stop, \
             patch.object(ui.tempfile, 'NamedTemporaryFile', side_effect=create_log):
            self.assertEqual(operator.execute(context), {'CANCELLED'})
        stop.assert_called_once_with(process)
        self.assertIn('window lost', operator.last_report[1])
        self.assertTrue(created_logs[0].closed)
        self.assertFalse(Path(created_logs[0].name).exists())
        self.assertIsNone(operator._log)
        self.assertIsNone(ui._active)
        self.assertTrue(ui.BJS_OT_env_converter_setup.poll(context))

    def test_windows_tree_stop_targets_only_owned_pid_and_does_not_kill_completed_process(self):
        ui = self.load_operator()
        process = types.SimpleNamespace(pid=123456789, poll=Mock(return_value=None), wait=Mock())
        with patch.object(ui.sys, 'platform', 'win32'), \
             patch.dict(os.environ, {'SystemRoot': 'C:/Windows'}), \
             patch.object(ui.converter_setup, 'process_options', return_value={'creationflags': 0x08000000}), \
             patch.object(ui.subprocess, 'run') as run:
            ui.stop_process(process)
            args = run.call_args.args[0]
            self.assertEqual(args[1:], ['/PID', str(process.pid), '/T', '/F'])
            self.assertEqual(Path(args[0]), Path('C:/Windows/System32/taskkill.exe'))
            self.assertEqual(run.call_args.kwargs['creationflags'], 0x08000000)
            self.assertNotIn('shell', run.call_args.kwargs)
            process.wait.assert_called_once_with(timeout=5)
            process.poll.return_value = 0
            ui.stop_process(process)
            run.assert_called_once()

    def test_addon_unregister_cancels_setup_before_unregistering_classes(self):
        # Execute only this integration function, without importing Blender's package.
        import ast
        tree = ast.parse((ADDON/'__init__.py').read_text(encoding='utf8'))
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'unregister')
        order = []
        bpy = types.SimpleNamespace(types=types.SimpleNamespace(
            TOPBAR_MT_file_export=types.SimpleNamespace(remove=lambda menu: order.append('menu'))))
        utils = types.ModuleType('bpy.utils')
        utils.unregister_class = lambda cls: order.append('class')
        namespace = {'cancel_setup': lambda: order.append('cancel'), 'classes': [object()],
            'bpy': bpy, 'menu_func': object()}
        exec(compile(ast.Module(body=[function], type_ignores=[]), '<unregister hook>', 'exec'), namespace)
        with patch.dict(sys.modules, {'bpy.utils': utils}):
            namespace['unregister']()
        self.assertEqual(order, ['cancel', 'class', 'menu'])


if __name__ == '__main__':
    unittest.main()
