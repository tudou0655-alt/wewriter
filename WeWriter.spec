# -*- mode: python ; coding: utf-8 -*-
import os
import re
import shutil
import subprocess
from PyInstaller.utils.hooks import collect_all
from PyInstaller.utils.win32.versioninfo import (FixedFileInfo, StringFileInfo, StringStruct, StringTable,
                                                 VarFileInfo, VarStruct, VSVersionInfo)

datas = []
binaries = []
hiddenimports = ['pystray._win32']
tmp_ret = collect_all('tzdata')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('playwright')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('webview')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
hiddenimports += ['clr', 'clr_loader', 'pythonnet']
datas += [('assets/wewriter-cat.png', 'assets'), ('wewriter/web', 'wewriter/web')]
# Build manifest (version, commit, dirty state) written by scripts/build.ps1; absent for ad-hoc builds.
_build_info = os.environ.get('WEWRITER_BUILD_INFO')
if _build_info and os.path.isfile(_build_info):
    datas += [(_build_info, '.')]


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

# Code signing (docs/代码签名与智能应用控制.md). Smart App Control checks every DLL/.pyd the one-file EXE
# unpacks at run time, not just WeWriter.exe, so unsigned bundled binaries are signed here before packing.
# Only when scripts/build.ps1 -Sign sets WEWRITER_SIGN; ordinary builds are unchanged.
def _sign_bundled(entries):
    picked = [(i, src) for i, (dest, src, kind) in enumerate(entries)
              if os.path.isfile(src) and src.lower().endswith(('.dll', '.pyd', '.exe'))]
    if not picked:
        return entries
    work = os.path.join(SPECPATH, 'build', 'signed-binaries')
    shutil.rmtree(work, ignore_errors=True)
    copies = []
    for n, (i, src) in enumerate(picked):
        target = os.path.join(work, str(n), os.path.basename(src))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy2(src, target)
        copies.append((i, target))
    listing = os.path.join(work, 'files.txt')
    with open(listing, 'w', encoding='utf-8') as f:
        f.write('\n'.join(target for _, target in copies))
    subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                    os.path.join(SPECPATH, 'scripts', 'sign.ps1'), '-ListFile', listing, '-OnlyUnsigned'], check=True)
    entries = list(entries)
    for i, target in copies:
        dest, _, kind = entries[i]
        entries[i] = (dest, target, kind)
    return entries


if os.environ.get('WEWRITER_SIGN') == '1':
    a.binaries = _sign_bundled(a.binaries)
    a.datas = _sign_bundled(a.datas)

# File properties (publisher, product, version) shown by Windows; the publisher should match the certificate.
_version = re.search(r'__version__\s*=\s*"([^"]+)"', open(os.path.join(SPECPATH, 'wewriter', '__init__.py'), encoding='utf-8').read()).group(1)
_numbers = tuple((list(map(int, re.findall(r'\d+', _version))) + [0, 0, 0, 0])[:4])
_publisher = os.environ.get('WEWRITER_PUBLISHER') or 'WeWriter'
version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=_numbers, prodvers=_numbers),
    kids=[StringFileInfo([StringTable('080404b0', [
        StringStruct('CompanyName', _publisher), StringStruct('FileDescription', 'WeWriter 公众号内容工作台'),
        StringStruct('FileVersion', _version), StringStruct('InternalName', 'WeWriter'),
        StringStruct('LegalCopyright', '© ' + _publisher), StringStruct('OriginalFilename', 'WeWriter.exe'),
        StringStruct('ProductName', 'WeWriter'), StringStruct('ProductVersion', _version)])]),
          VarFileInfo([VarStruct('Translation', [0x0804, 1200])])])

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='WeWriter',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX-packed files are a common false-positive trigger for Windows reputation checks
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets/wewriter-cat.ico'],
    version=version_info,
)
