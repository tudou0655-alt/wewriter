"""公众号工作台 — local-first Windows application."""
# Single version source: scripts/build.ps1, scripts/build_to_desktop.cmd, the UI and
# scripts/verify_delivery.py all read this value; never repeat it elsewhere.
__version__ = "0.10.4"
# Written by scripts/build.ps1 (via scripts/verify_delivery.py) and bundled into the EXE.
BUILD_INFO_NAME = 'build-info.json'


def build_label(info=None, frozen=False):
    """Human-readable version with build provenance. A dirty or unknown worktree is never
    presented as a clean commit."""
    label = 'v' + __version__
    if not frozen:
        return label + ' · 源码运行'
    if not isinstance(info, dict) or info.get('version') != __version__:
        return label + ' · 构建信息未记录'
    commit = str(info.get('commit') or '')[:7] or '提交未知'
    state = {True: '含未提交改动', False: '干净提交'}.get(info.get('dirty'), '工作区状态未知')
    return f'{label} · {commit} · {state}'
