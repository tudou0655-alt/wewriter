"""Deterministic offline delivery checks.

Never calls an API, opens the network, launches Edge or reads user credentials.
Exit code 0 when every check passes, 1 otherwise.

    .\\.venv\\Scripts\\python.exe scripts\\verify_delivery.py [--root PROJECT_DIR]

scripts/build.ps1 also uses two build helpers (read-only Git commands, no network):

    --write-build-info PATH        write the build manifest bundled into the package
    --check-package DIR --zip ZIP  verify a built release directory and ZIP
"""
import argparse
import importlib
import importlib.metadata
import json
import re
import sqlite3
import subprocess
import sys
import tempfile
import zipfile
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MIN_PYTHON = (3, 13)
REQUIRED_FILES = (
    'main.py', 'README.md', 'WeWriter.spec', 'requirements.txt', 'requirements-lock.txt',
    'assets/wewriter-cat.ico', 'scripts/build.ps1', 'scripts/build_to_desktop.cmd', 'scripts/install.ps1',
    'wewriter/__init__.py', 'wewriter/db.py', 'wewriter/logs.py', 'wewriter/ui.py',
)
MODULES = ('wewriter', 'wewriter.config', 'wewriter.logs', 'wewriter.db', 'wewriter.api',
           'wewriter.research', 'wewriter.hotspots', 'wewriter.engine', 'wewriter.secrets',
           'wewriter.windows', 'wewriter.weread', 'wewriter.ui', 'main')


class CheckFailed(Exception):
    pass


def check_python(root):
    if sys.version_info[:2] < MIN_PYTHON:
        raise CheckFailed('需要 Python %d.%d 或更高，当前 %s' % (*MIN_PYTHON, sys.version.split()[0]))
    return sys.version.split()[0]


def check_files(root):
    missing = [name for name in REQUIRED_FILES if not (root / name).is_file()]
    if missing:
        raise CheckFailed('缺少文件：' + '、'.join(missing))
    return '%d 个必需文件齐全' % len(REQUIRED_FILES)


CHINA_TZ = timezone(timedelta(hours=8))
PACKAGE_FILES = ('WeWriter.exe', 'install.ps1', 'README.md', 'build-info.json')
_VERSION_LITERAL = re.compile(r'v?\d+\.\d+\.\d+')


def read_version(root):
    init = (root / 'wewriter' / '__init__.py').read_text(encoding='utf-8')
    package = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', init, re.M)
    if not package:
        raise CheckFailed('无法从 wewriter/__init__.py 读取版本号')
    return package.group(1)


def check_version(root):
    """wewriter.__version__ is the only version definition; everything else must read it."""
    version = read_version(root)
    problems = []
    build = (root / 'scripts' / 'build.ps1').read_text(encoding='utf-8')
    if re.search(r'\$version\s*=\s*["\']', build) or '__version__' not in build:
        problems.append('build.ps1 未从 wewriter.__version__ 读取版本')
    desktop = (root / 'scripts' / 'build_to_desktop.cmd').read_text(encoding='utf-8')
    if _VERSION_LITERAL.search(desktop) or '__version__' not in desktop:
        problems.append('build_to_desktop.cmd 含独立版本号或未读取 wewriter.__version__')
    ui = (root / 'wewriter' / 'ui.py').read_text(encoding='utf-8')
    if re.search(r'["\'][^"\'\n]*v\d+\.\d+\.\d+', ui):
        problems.append('ui.py 含硬编码版本号')
    readme = (root / 'README.md').read_text(encoding='utf-8').splitlines()
    if not readme or 'v' + version not in readme[0]:
        problems.append('README 标题版本不是 v%s' % version)
    if problems:
        raise CheckFailed('版本号不一致：' + '；'.join(problems))
    return version


def _git(root, *args):
    result = subprocess.run(['git', *args], cwd=root, capture_output=True, text=True, timeout=30,
                            encoding='utf-8', errors='replace')
    if result.returncode != 0:
        raise OSError('git %s failed' % args[0])
    return result.stdout


def build_info(root, git=_git, now=None):
    """Traceable build manifest. Contains no paths, machine names, credentials or user data.

    ``dirty`` is True when tracked or untracked files differ from the commit, None when Git
    is unavailable; a dirty or unknown worktree is never reported as a clean commit."""
    version = read_version(root)
    try:
        commit = git(root, 'rev-parse', 'HEAD').strip()
        dirty = bool(git(root, 'status', '--porcelain', '--untracked-files=normal').strip())
    except (OSError, subprocess.SubprocessError):
        commit, dirty = '', None
    built = (now or datetime.now(timezone.utc)).astimezone(CHINA_TZ)
    suffix = {True: '.dirty', False: '', None: '.unknown'}[dirty]
    return {'name': 'WeWriter', 'version': version, 'commit': commit, 'dirty': dirty,
            'build_id': version + ('+g' + commit[:7] if commit else '+nogit') + suffix,
            'built_at': built.isoformat(timespec='seconds'), 'timezone': 'Asia/Shanghai (UTC+8)'}


def write_build_info(root, target, git=_git, now=None):
    info = build_info(root, git, now)
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(info, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return info


def check_package(root, release_dir, zip_path):
    """Verify a built release directory and ZIP against the single version source."""
    version = read_version(root)
    release_dir, zip_path = Path(release_dir), Path(zip_path)
    problems = []
    if release_dir.name != 'v' + version:
        problems.append('发布目录名不是 v%s' % version)
    if zip_path.name != 'WeWriter-v%s-Windows.zip' % version:
        problems.append('ZIP 文件名不含 v%s' % version)
    missing = [name for name in PACKAGE_FILES if not (release_dir / name).is_file()]
    if missing:
        problems.append('发布目录缺少：' + '、'.join(missing))
    try:
        info = json.loads((release_dir / 'build-info.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        info = {}
    if info.get('version') != version:
        problems.append('build-info.json 版本不是 %s' % version)
    if 'dirty' not in info or not (info.get('commit') or info.get('dirty') is None):
        problems.append('build-info.json 缺少提交或工作区状态')
    if (release_dir / 'README.md').is_file():
        first = (release_dir / 'README.md').read_text(encoding='utf-8').splitlines()[:1]
        if not first or 'v' + version not in first[0]:
            problems.append('包内 README 版本不是 v%s' % version)
    try:
        with zipfile.ZipFile(zip_path) as archive:
            names = sorted(archive.namelist())
    except (OSError, zipfile.BadZipFile):
        names = None
    if names is None:
        problems.append('ZIP 无法读取')
    elif names != sorted(PACKAGE_FILES):
        problems.append('ZIP 内容应为 %s，实际 %s' % ('、'.join(sorted(PACKAGE_FILES)), '、'.join(names)))
    if problems:
        raise CheckFailed('；'.join(problems))
    state = {True: '含未提交改动', False: '干净提交', None: '工作区状态未知'}[info.get('dirty')]
    return '包 v%s 内容一致（%s，%s）' % (version, info.get('build_id', ''), state)


def _canonical(name):
    return re.sub(r'[-_.]+', '-', name).lower()


def check_dependencies(root):
    mismatched = []
    count = 0
    for line in (root / 'requirements-lock.txt').read_text(encoding='utf-8').splitlines():
        line = line.split('#', 1)[0].strip()
        if '==' not in line:
            continue
        name, expected = (part.strip() for part in line.split('==', 1))
        count += 1
        try:
            installed = importlib.metadata.version(_canonical(name))
        except importlib.metadata.PackageNotFoundError:
            mismatched.append('%s 未安装' % name)
            continue
        if installed != expected:
            mismatched.append('%s 已安装 %s，锁定 %s' % (name, installed, expected))
    if mismatched:
        raise CheckFailed('；'.join(mismatched))
    return '%d 个锁定依赖版本一致' % count


def check_imports(root):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    for name in MODULES:
        try:
            importlib.import_module(name)
        except Exception as exc:
            raise CheckFailed('无法导入 %s（%s）' % (name, type(exc).__name__)) from None
    return '%d 个模块可导入' % len(MODULES)


def check_demo_database(root):
    from main import demo_data
    from wewriter.db import SCHEMA_VERSION, Database

    workspace = root / '.local-data' / 'delivery-check'
    workspace.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=workspace) as temporary:
        temp = Path(temporary)
        db = Database(temp / 'demo.sqlite')
        demo_data(db)
        if len(db.accounts()) != 2 or not db.rows('SELECT id FROM drafts'):
            raise CheckFailed('演示数据未完整写入')
        db.backup(temp / 'backup.sqlite')
        db.add_account('交付检查临时账号', 'delivery_check_temp')
        db.restore(temp / 'backup.sqlite')
        if len(db.accounts()) != 2:
            raise CheckFailed('备份恢复后数据不一致')
        with closing(sqlite3.connect(db.path)) as raw:
            version = raw.execute('PRAGMA user_version').fetchone()[0]
        if version != SCHEMA_VERSION:
            raise CheckFailed('数据库版本 %d，应为 %d' % (version, SCHEMA_VERSION))
    return '演示库初始化、备份、恢复和迁移正常（版本 %d）' % SCHEMA_VERSION


CHECKS = (
    ('Python 版本', check_python),
    ('必需文件', check_files),
    ('版本号一致', check_version),
    ('锁定依赖', check_dependencies),
    ('模块导入', check_imports),
    ('演示数据库', check_demo_database),
)


def _describe(exc, root):
    if isinstance(exc, CheckFailed):
        text = str(exc)
    else:
        text = '%s：%s' % (type(exc).__name__, exc)
    try:
        from wewriter.logs import redact
        text = redact(text)
    except Exception:
        pass
    # Keep output free of absolute local paths.
    return text.replace(str(root), '<项目目录>').replace(str(Path.home()), '<用户目录>')[:300]


def run_checks(root):
    results = []
    for name, check in CHECKS:
        try:
            results.append((name, True, check(root)))
        except Exception as exc:
            results.append((name, False, _describe(exc, root)))
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description='离线交付检查：不联网、不调用 API、不读取凭据')
    parser.add_argument('--root', type=Path, default=PROJECT_ROOT, help='项目目录（默认当前仓库）')
    parser.add_argument('--write-build-info', type=Path, help='构建用：写入构建清单 JSON')
    parser.add_argument('--check-package', type=Path, help='构建用：校验发布目录')
    parser.add_argument('--zip', type=Path, help='与 --check-package 一起使用：发布 ZIP')
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if args.write_build_info or args.check_package:
        try:
            if args.write_build_info:
                info = write_build_info(root, args.write_build_info)
                print('[通过] 构建清单：%s' % info['build_id'])
            if args.check_package:
                if not args.zip:
                    raise CheckFailed('--check-package 需要同时提供 --zip')
                print('[通过] 发布包：%s' % check_package(root, args.check_package, args.zip))
        except Exception as exc:
            print('[失败] 构建检查：%s' % _describe(exc, root))
            return 1
        return 0
    results = run_checks(root)
    for name, ok, detail in results:
        print('[%s] %s：%s' % ('通过' if ok else '失败', name, detail))
    failed = sum(1 for _, ok, _ in results if not ok)
    print('结果：%d 项通过，%d 项失败' % (len(results) - failed, failed))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
