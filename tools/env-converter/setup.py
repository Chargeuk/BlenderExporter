"""Explicit local setup/repair for the adjacent Babylon ENV converter (no Blender)."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

PINS = {'babylonjs': '7.27.0', 'playwright-core': '1.56.1'}
CONFIG = 'runtime.local.json'
LOG = 'setup.local.log'


class SetupError(RuntimeError):
    pass


def process_options():
    return {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}


def run(command, folder, log, timeout=60):
    try:
        result = subprocess.run([str(value) for value in command], cwd=folder,
            capture_output=True, text=True, timeout=timeout, **process_options())
    except (OSError, subprocess.SubprocessError) as exc:
        raise SetupError('Cannot run '+str(command[0])+': '+str(exc)) from exc
    log.extend([result.stdout, result.stderr])
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()[-3000:]
        raise SetupError('Command failed ('+str(result.returncode)+'): '+str(command[0])+'\n'+detail)
    return result.stdout.strip()


def node_executable(value=None):
    candidate = value or shutil.which('node')
    if not candidate or not Path(candidate).is_file():
        raise SetupError('Node.js 18+ is required. Install Node.js and npm, then run setup again.')
    return Path(candidate).resolve()


def npm_command(node, explicit=None):
    """Use npm's JS entrypoint on Windows; npm.cmd is not a native executable."""
    if explicit:
        script = Path(explicit).expanduser().resolve()
        if not script.is_file():
            raise SetupError('The selected npm CLI file does not exist: '+str(script))
        return [node, script]
    candidates = [node.parent/'node_modules/npm/bin/npm-cli.js']
    npm = shutil.which('npm.cmd' if os.name == 'nt' else 'npm')
    if npm:
        candidates.append(Path(npm).parent/'node_modules/npm/bin/npm-cli.js')
        resolved = Path(npm).resolve()
        if resolved.name == 'npm-cli.js':
            candidates.append(resolved)
    for script in candidates:
        if script.is_file():
            return [node, script.resolve()]
    if npm and os.name != 'nt':
        return [npm]
    raise SetupError('npm is required. Install the Node.js npm component, or select npm-cli.js with --npm-cli.')


def managed_folder(converter):
    converter = Path(converter).expanduser().resolve()
    folder = converter.parent
    if not converter.is_file() or not (folder/'runtime.cjs').is_file() or not (folder/'setup.py').is_file():
        raise SetupError('Selected converter has no supported setup companion: '+str(converter)
                         +'. Use its own setup procedure or select the bundled converter explicitly.')
    try:
        package = json.loads((folder/'package.json').read_text(encoding='utf8'))
        lock = json.loads((folder/'package-lock.json').read_text(encoding='utf8'))
        if package['dependencies'] != PINS or lock['packages']['']['dependencies'] != PINS:
            raise ValueError('dependency pins differ')
        if any(lock['packages']['node_modules/'+name]['version'] != version for name, version in PINS.items()):
            raise ValueError('locked package version differs')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SetupError('Selected converter needs matching package.json and package-lock.json: '+str(exc)) from exc
    return converter, folder


def browser_candidates(folder, expected):
    """Prefer the existing selection/cache; launch checks decide compatibility."""
    candidates = []
    saved = folder/CONFIG
    if saved.is_file():
        try:
            candidates.append(json.loads(saved.read_text(encoding='utf8'))['browser'])
        except (OSError, ValueError, KeyError, TypeError):
            pass  # An explicit repair can replace malformed local config after a successful probe.
    candidates.append(expected)
    caches = [Path.home()/'.cache/ms-playwright']
    if os.environ.get('LOCALAPPDATA'):
        caches.insert(0, Path(os.environ['LOCALAPPDATA'])/'ms-playwright')
    if os.environ.get('PLAYWRIGHT_BROWSERS_PATH') and os.environ['PLAYWRIGHT_BROWSERS_PATH'] != '0':
        caches.insert(0, Path(os.environ['PLAYWRIGHT_BROWSERS_PATH']).expanduser())
    for cache in caches:
        for pattern in ('chromium-*/chrome-win/chrome.exe', 'chromium-*/chrome-win64/chrome.exe',
                        'chromium-*/chrome-linux/chrome', 'chromium-*/chrome-linux64/chrome',
                        'chromium-*/chrome-mac/Chromium.app/Contents/MacOS/Chromium'):
            candidates.extend(sorted(cache.glob(pattern), reverse=True))
    for variable in ('PROGRAMFILES', 'PROGRAMFILES(X86)', 'LOCALAPPDATA'):
        if os.environ.get(variable):
            base = Path(os.environ[variable])
            candidates.extend([base/'Google/Chrome/Application/chrome.exe',
                               base/'Microsoft/Edge/Application/msedge.exe'])
    candidates.extend(['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
                       '/Applications/Chromium.app/Contents/MacOS/Chromium'])
    candidates.extend(shutil.which(name) for name in ('chromium', 'chromium-browser', 'google-chrome', 'google-chrome-stable'))
    result = []
    for value in candidates:
        if not isinstance(value, (str, Path)) or not str(value):
            continue
        path = Path(value).expanduser().resolve()
        if path.is_file() and path not in result:
            result.append(path)
    return result


def check(node, converter, log, browser=None):
    command = [node, converter, '--check', '--launch']
    if browser:
        command.extend(['--browser', browser])
    try:
        data = json.loads(run(command, converter.parent, log))
    except ValueError as exc:
        raise SetupError('Converter readiness did not return JSON') from exc
    if (not isinstance(data, dict) or data.get('status') != 'ready' or data.get('babylon') != PINS['babylonjs']
            or data.get('playwright') != PINS['playwright-core'] or data.get('webgl') != 2):
        raise SetupError('Converter readiness requires the pinned packages and a working WebGL2 browser')
    return data


def setup_converter(converter=None, *, browser=None, download_browser=False,
                    node=None, npm_cli=None, check_only=False, progress=None):
    converter, folder = managed_folder(converter or Path(__file__).with_name('convert.cjs'))
    log = []
    notify = progress or (lambda message: None)
    try:
        notify('Setup target: '+str(converter))
        if node is None and (folder/CONFIG).is_file():
            try:
                saved = json.loads((folder/CONFIG).read_text(encoding='utf8'))
                configured = saved.get('node') if isinstance(saved, dict) else None
                if configured and Path(configured).is_file():
                    node = configured
            except (OSError, ValueError, TypeError):
                pass
        node = node_executable(node)
        version = run([node, '--version'], folder, log)
        try:
            if int(version.lstrip('v').split('.')[0]) < 18:
                raise ValueError('Node.js is older than 18')
        except ValueError as exc:
            raise SetupError('Node.js 18+ is required; found '+version) from exc
        if check_only:
            return dict(check(node, converter, log, browser), converter=str(converter), node=str(node), node_version=version)
        npm = npm_command(node, npm_cli)
        npm_version = run(npm+['--version'], folder, log)
        notify('Installing exact locked ENV dependencies with npm ci (no lifecycle scripts)')
        original = {name: (folder/name).read_bytes() for name in ('package.json', 'package-lock.json')}
        run(npm+['ci', '--ignore-scripts', '--no-audit', '--no-fund'], folder, log, timeout=900)
        if any((folder/name).read_bytes() != data for name, data in original.items()):
            raise SetupError('npm changed the converter manifest/lockfile; setup stopped')
        expected = run([node, '-e', "process.stdout.write(require('playwright-core').chromium.executablePath())"], folder, log)
        environment_browser = os.environ.get('BJS_ENV_CHROMIUM')
        if browser and environment_browser and Path(browser).resolve() != Path(environment_browser).resolve():
            raise SetupError('BJS_ENV_CHROMIUM selects another browser. Unset it or select that same browser explicitly.')
        requested = browser or environment_browser
        candidates = [Path(requested).expanduser().resolve()] if requested else browser_candidates(folder, expected)
        selected = None
        failures = []
        for candidate in candidates:
            notify('Checking WebGL2 browser: '+str(candidate))
            try:
                check(node, converter, log, candidate)
                selected = candidate
                break
            except SetupError as exc:
                failures.append(str(candidate)+': '+str(exc))
        if selected is None and download_browser and not requested:
            notify('Downloading pinned Playwright Chromium (explicitly requested)')
            run([node, folder/'node_modules/playwright-core/cli.js', 'install', 'chromium'], folder, log, timeout=900)
            selected = Path(expected).resolve()
            check(node, converter, log, selected)
        if selected is None:
            raise SetupError('No compatible Chromium/Chrome found. Select --browser or explicitly allow --download-browser.\n'
                             +'\n'.join(failures[-3:]))
        config = folder/CONFIG
        previous = config.read_bytes() if config.exists() else None
        try:
            config.write_text(json.dumps({'schema_version': 1, 'browser': str(selected), 'node': str(node)}, indent=2)+'\n', encoding='utf8')
            # Check the persisted/default selection, not only the temporary --browser override.
            ready = check(node, converter, log)
        except Exception:
            if previous is None:
                config.unlink(missing_ok=True)
            else:
                config.write_bytes(previous)
            raise
        notify('ENV converter ready: '+str(selected))
        return dict(ready, converter=str(converter), node=str(node), node_version=version,
                    npm_version=npm_version, runtime_config=str(config), log=str(folder/LOG))
    finally:
        if not check_only:
            try:
                (folder/LOG).write_text('\n'.join(log), encoding='utf8')
            except OSError:
                pass  # Never mask the actionable install/readiness error with a log-write error.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--converter', type=Path, help='Selected managed converter; defaults to adjacent convert.cjs')
    parser.add_argument('--browser', type=Path, help='Existing compatible Chromium/Chrome executable; no download')
    parser.add_argument('--download-browser', action='store_true', help='Download pinned Chromium only if no existing candidate works')
    parser.add_argument('--node', type=Path, help='Node.js executable when it is not on PATH')
    parser.add_argument('--npm-cli', type=Path, help='npm-cli.js when npm cannot be discovered')
    parser.add_argument('--check', action='store_true', help='Read-only launch/readiness check; no install/config writes')
    args = parser.parse_args()
    try:
        result = setup_converter(args.converter, browser=args.browser, download_browser=args.download_browser,
            node=args.node, npm_cli=args.npm_cli, check_only=args.check,
            progress=lambda message: print(message, file=sys.stderr, flush=True))
    except (SetupError, OSError) as exc:
        parser.exit(1, 'ENV setup failed: '+str(exc)+'\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
