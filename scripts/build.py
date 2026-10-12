"""Build a self-contained macOS app without copying personal browser profiles."""
import argparse
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--runtime', type=Path, help='Reuse a standalone CPython 3.12 directory (not a venv).')
parser.add_argument('--browser-cache', type=Path, help='Reuse a Camoufox cache containing the paired browser.')
args = parser.parse_args()
if sys.platform != 'darwin':
    parser.error('This launcher currently supports macOS only.')
for command in ('uv', 'xcrun', 'iconutil', 'codesign'):
    if not shutil.which(command):
        parser.error(f'Missing {command}; install uv and Xcode Command Line Tools first.')

build = ROOT / 'build'
build.mkdir(exist_ok=True)
env = dict(os.environ)
if args.runtime:
    runtime_source = args.runtime.expanduser().resolve()
else:
    python_root = build / 'python'
    env['UV_PYTHON_INSTALL_DIR'] = str(python_root)
    subprocess.run(['uv', 'python', 'install', '--install-dir', str(python_root), '--no-bin', '3.12'],
                   env=env, check=True)
    interpreter = subprocess.check_output(['uv', 'python', 'find', '--managed-python', '--no-project', '3.12'],
                                          env=env, text=True).strip()
    runtime_source = Path(interpreter).resolve().parent.parent
if not (runtime_source / 'bin/python3.12').is_file() or (runtime_source / 'pyvenv.cfg').exists():
    parser.error('--runtime must point at a standalone CPython 3.12 installation.')

bundle = build / 'Camoufox Sessions.app'
if bundle.exists():
    shutil.rmtree(bundle)
resources = bundle / 'Contents/Resources'
macos = bundle / 'Contents/MacOS'
resources.mkdir(parents=True)
macos.mkdir()
shutil.copytree(runtime_source, resources / 'runtime', symlinks=True)
python = resources / 'runtime/bin/python'
if not python.exists():
    python.symlink_to('python3.12')
subprocess.run(['uv', 'pip', 'install', '--python', str(python), '--break-system-packages',
                '--require-hashes', '-r', str(ROOT / 'requirements.txt')], check=True)

cache = resources / 'browser-cache'
if args.browser_cache:
    # Copy only engine packages and selection metadata, never profile data,
    # unrelated custom repositories, downloads, logs, or credentials.
    original = args.browser_cache.expanduser().resolve()
    shutil.copytree(original / 'browsers/official', cache / 'browsers/official', symlinks=True)
    selection = json.loads((original / 'config.json').read_text()).get('active_version', '')
    if not selection.startswith('browsers/official/') or '..' in Path(selection).parts:
        parser.error('Reused cache must select an official Camoufox browser.')
    (cache / 'config.json').write_text(json.dumps({'active_version': selection}))
    shutil.copy2(original / '.0.5_FLAG', cache / '.0.5_FLAG')
else:
    subprocess.run([str(python), str(ROOT / 'scripts/fetch_browser.py'), str(cache)], check=True)
shutil.copy2(ROOT / 'src/session_worker.py', resources / 'session_worker.py')
subprocess.run([str(python), '-c',
    'from pathlib import Path; import sys; sys.path.insert(0, sys.argv[1]); '
    'import session_worker; '
    'import tempfile; '
    'temp = tempfile.TemporaryDirectory(); '
    'options = session_worker.make_options(Path(temp.name), Path(sys.argv[1])); '
    'assert Path(options["executable_path"]).is_file()', str(resources)], check=True)

target_info = json.loads(subprocess.check_output(['xcrun', 'swiftc', '-print-target-info'], text=True))
architecture = target_info['target']['triple'].split('-')[0]
subprocess.run(['xcrun', 'swiftc', '-target', f'{architecture}-apple-macosx14.0',
                '-parse-as-library', str(ROOT / 'src/Launcher.swift'),
                '-o', str(macos / 'CamoufoxSessions'), '-framework', 'SwiftUI', '-framework', 'AppKit'], check=True)
iconset = build / 'AppIcon.iconset'
subprocess.run(['xcrun', 'swift', str(ROOT / 'src/Icon.swift'), str(iconset)], check=True)
subprocess.run(['iconutil', '-c', 'icns', str(iconset), '-o', str(resources / 'AppIcon.icns')], check=True)
info = {'CFBundleDisplayName':'Camoufox Sessions','CFBundleName':'Camoufox Sessions',
        'CFBundleExecutable':'CamoufoxSessions','CFBundleIconFile':'AppIcon',
        'CFBundleIdentifier':'io.github.jiiiin.camoufox-sessions','CFBundlePackageType':'APPL',
        'CFBundleShortVersionString':'0.1.0','CFBundleVersion':'1',
        'LSMinimumSystemVersion':'14.0','NSHighResolutionCapable':True,
        'NSHumanReadableCopyright':'Copyright 2026 Jiiiin. Launcher: MIT; engine: MPL-2.0.'}
(bundle / 'Contents/Info.plist').write_bytes(plistlib.dumps(info))
for name in ('LICENSE', 'THIRD_PARTY_NOTICES.md'):
    shutil.copy2(ROOT / name, resources / name)
subprocess.run(['codesign', '--force', '--sign', '-', str(bundle)], check=True)
subprocess.run(['codesign', '--verify', str(bundle)], check=True)
print(f'Built {bundle}\nCopy it to Applications when the current launcher is closed.')
