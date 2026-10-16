"""Deterministic offline delivery checks.

Never calls an API, opens the network, launches Edge or reads user credentials.
Exit code 0 when every check passes, 1 otherwise.

    .\\.venv\\Scripts\\python.exe scripts\\verify_delivery.py [--root PROJECT_DIR]
"""
import argparse
import importlib
import importlib.metadata
import re
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MIN_PYTHON = (3, 13)
REQUIRED_FILES = (
    'main.py', 'README.md', 'WeWriter.spec', 'requirements.txt', 'requirements-lock.txt',
    'assets/wewriter-cat.ico', 'scripts/build.ps1', 'scripts/install.ps1',
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


def check_version(root):
    init = (root / 'wewriter' / '__init__.py').read_text(encoding='utf-8')
    build = (root / 'scripts' / 'build.ps1').read_text(encoding='utf-8')
    package = re.search(r'__version__\s*=\s*["\']([^"\']+)', init)
    script = re.search(r'\$version\s*=\s*["\']([^"\']+)', build)
    if not package or not script:
        raise CheckFailed('无法读取版本号')
    if package.group(1) != script.group(1):
        raise CheckFailed('版本号不一致：wewriter=%s，build.ps1=%s' % (package.group(1), script.group(1)))
    return package.group(1)


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
    args = parser.parse_args(argv)
    root = args.root.resolve()
    results = run_checks(root)
    for name, ok, detail in results:
        print('[%s] %s：%s' % ('通过' if ok else '失败', name, detail))
    failed = sum(1 for _, ok, _ in results if not ok)
    print('结果：%d 项通过，%d 项失败' % (len(results) - failed, failed))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
