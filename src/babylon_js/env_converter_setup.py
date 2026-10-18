"""Explicit non-blocking Preferences action; setup runs in a separate Python process."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import bpy

from . import env_export
from .workflow_tools.env_converter import setup as converter_setup

_active = None


def setup_python():
    # Blender's sys.executable is Blender itself, not its bundled Python binary.
    names = ('python.exe', 'python'+str(sys.version_info.major)+'.'+str(sys.version_info.minor), 'python3')
    for name in names:
        candidate = Path(sys.prefix)/'bin'/name
        if candidate.is_file():
            return str(candidate)
    for name in ('python3', 'python'):
        candidate = shutil.which(name)
        if candidate:
            return candidate
    raise converter_setup.SetupError('A Python 3.8+ executable is required to run ENV setup outside Blender.')


def stop_process(process):
    if process.poll() is not None:
        return
    if sys.platform == 'win32':
        # Popen retains the owned process handle; target only its PID and descendants.
        taskkill = Path(os.environ['SystemRoot'])/'System32/taskkill.exe'
        subprocess.run([str(taskkill), '/PID', str(process.pid), '/T', '/F'],
            capture_output=True, timeout=15, check=True, **converter_setup.process_options())
    else:
        os.killpg(process.pid, signal.SIGKILL)
    process.wait(timeout=5)


def cancel_setup():
    """Called before add-on classes are unregistered, while their operator still exists."""
    if _active is not None:
        _active._cleanup(terminate=True)


class BJS_OT_env_converter_setup(bpy.types.Operator):
    bl_idname = 'babylon.env_converter_setup'
    bl_label = 'Set up / repair ENV converter'
    bl_description = 'Install locked dependencies for the selected ENV converter and verify an existing browser'
    _running = False

    @classmethod
    def poll(cls, context):
        return not cls._running

    def _cleanup(self, terminate=False):
        global _active
        # Detach first so cancellation, errors and unregister can safely repeat cleanup.
        timer, self._timer = getattr(self, '_timer', None), None
        process, self._process = getattr(self, '_process', None), None
        log, self._log = getattr(self, '_log', None), None
        type(self)._running = False
        if _active is self:
            _active = None
        errors = []
        def attempt(action):
            try:
                action()
            except Exception as exc:
                errors.append(str(exc))
                return False
            return True
        if timer is not None:
            attempt(lambda: self._window_manager.event_timer_remove(timer))
        if terminate and process is not None:
            stopped = attempt(lambda: stop_process(process))
            # If tree termination failed, still reap/stop the owned parent if possible.
            if not stopped:
                def stop_parent():
                    if process.poll() is None:
                        process.kill()
                        process.wait(timeout=5)
                attempt(stop_parent)
        if log is not None:
            attempt(log.close)
            attempt(lambda: Path(log.name).unlink(missing_ok=True))
        attempt(env_export._probe.cache_clear)
        self._cleanup_errors = errors
        if errors:
            # Cleanup must continue even if Blender's window/operator has disappeared.
            try:
                print('ENV setup cleanup: '+'; '.join(errors))
            except Exception:
                pass

    def _error(self, message):
        self._cleanup(terminate=True)
        try:
            self.report({'ERROR'}, str(message))
        except Exception:
            pass
        return {'CANCELLED'}

    def cancel(self, context):
        self._cleanup(terminate=True)

    def execute(self, context):
        global _active
        try:
            if context.window is None:
                return self._error('Run setup.py with external Python for background/headless setup')
            preferences = context.preferences.addons['babylon_js'].preferences
            script = env_export.helper_path()
            browser = bpy.path.abspath(preferences.env_converter_browser) if preferences.env_converter_browser else None
            download = preferences.env_converter_download_browser
            converter_setup.managed_folder(script)
            self._output = ''
            self._position = 0
            self._window = context.window
            self._window_manager = context.window_manager
            _active = self
            type(self)._running = True
            self._log = tempfile.NamedTemporaryFile(delete=False)
            command = [setup_python(), '-u', '-B', converter_setup.__file__, '--converter', str(script)]
            if browser:
                command.extend(['--browser', browser])
            if download:
                command.append('--download-browser')
            process_options = converter_setup.process_options()
            if sys.platform != 'win32':
                process_options['start_new_session'] = True
            self._process = subprocess.Popen(command, cwd=script.parent, stdout=self._log,
                stderr=subprocess.STDOUT, env=dict(os.environ, PYTHONIOENCODING='utf-8'),
                **process_options)
            self._timer = context.window_manager.event_timer_add(.25, window=context.window)
            context.window_manager.modal_handler_add(self)
            preferences.env_converter_status = 'Setting up: '+str(script)
            return {'RUNNING_MODAL'}
        except Exception as exc:
            return self._error(exc)

    def modal(self, context, event):
        if _active is not self:
            return {'CANCELLED'}
        try:
            # Disabling the add-on or closing/changing its window cancels owned work.
            preferences = context.preferences.addons['babylon_js'].preferences
            if context.window != self._window:
                return self._error('ENV setup window closed or changed')
            if event.type == 'ESC':
                self.cancel(context)
                return {'CANCELLED'}
            if event.type != 'TIMER':
                return {'PASS_THROUGH'}
            returncode = self._process.poll()
            # A separate read handle must not move the child's stdout file position.
            with open(self._log.name, 'rb') as stream:
                stream.seek(self._position)
                self._output += stream.read().decode('utf8', errors='replace')
                self._position = stream.tell()
            if self._output.strip():
                preferences.env_converter_status = self._output.strip().splitlines()[-1][:300]
            if returncode is None:
                return {'RUNNING_MODAL'}
            self._cleanup()
            try:
                result = json.loads(self._output.strip().splitlines()[-1]) if returncode == 0 else {}
            except (ValueError, IndexError):
                result = {}
            if not isinstance(result, dict) or result.get('status') != 'ready':
                error = self._output.strip()[-1500:] or 'ENV setup failed without diagnostics'
                preferences.env_converter_status = 'Setup failed; see converter setup.local.log'
                self.report({'ERROR'}, error)
                return {'CANCELLED'}
            preferences.env_converter_status = 'Ready: '+result['chromium']
            self.report({'INFO'}, 'ENV converter ready; locked dependencies installed locally')
            return {'FINISHED'}
        except Exception as exc:
            return self._error(exc)
