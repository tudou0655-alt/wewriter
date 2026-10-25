"""Offline delivery check script tests."""
import contextlib
import importlib.util
import io
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location('verify_delivery', ROOT / 'scripts' / 'verify_delivery.py')
verify_delivery = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(verify_delivery)


def run(argv):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = verify_delivery.main(argv)
    return code, output.getvalue()


class DeliveryCheckTest(unittest.TestCase):
    def setUp(self):
        workspace_temp = ROOT / '.local-data' / 'tests'
        workspace_temp.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=workspace_temp)
        self.path = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def fake_project(self, skip=()):
        for name in verify_delivery.REQUIRED_FILES:
            if name in skip:
                continue
            target = self.path / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)
        return self.path

    def test_repository_passes(self):
        code, output = run([])
        self.assertEqual(code, 0, output)
        self.assertNotIn('[失败]', output)
        self.assertIn('6 项通过，0 项失败', output)
        self.assertNotIn(str(ROOT), output)

    def test_missing_file_fails_without_leaking_paths(self):
        root = self.fake_project(skip=('WeWriter.spec', 'scripts/install.ps1'))
        code, output = run(['--root', str(root)])
        self.assertEqual(code, 1)
        self.assertIn('[失败] 必需文件：缺少文件：WeWriter.spec、scripts/install.ps1', output)
        self.assertNotIn(str(root), output)

    def test_version_mismatch_fails(self):
        # A second, hard-coded version string is a failure even before it drifts.
        root = self.fake_project()
        build = root / 'scripts' / 'build.ps1'
        build.write_text("$version = '9.9.9-fake'\n" + build.read_text(encoding='utf-8'), encoding='utf-8')
        code, output = run(['--root', str(root)])
        self.assertEqual(code, 1)
        self.assertIn('[失败] 版本号一致', output)
        self.assertIn('build.ps1', output)

    def test_readme_version_mismatch_fails(self):
        root = self.fake_project()
        readme = root / 'README.md'
        lines = readme.read_text(encoding='utf-8').splitlines()
        readme.write_text('\n'.join(['# 公众号工作台 v9.9.9'] + lines[1:]), encoding='utf-8')
        code, output = run(['--root', str(root)])
        self.assertEqual(code, 1)
        self.assertIn('README 标题版本', output)

    def test_unexpected_error_is_reported_not_raised(self):
        def boom(root):
            raise RuntimeError('unexpected at %s with sk-FAKE0000DELIVERY0000DUMMY' % root)

        original = verify_delivery.CHECKS
        verify_delivery.CHECKS = (('故障注入', boom),)
        try:
            code, output = run([])
        finally:
            verify_delivery.CHECKS = original
        self.assertEqual(code, 1)
        self.assertIn('[失败] 故障注入：RuntimeError', output)
        self.assertNotIn(str(ROOT), output)
        self.assertNotIn('sk-FAKE0000DELIVERY0000DUMMY', output)


if __name__ == '__main__':
    unittest.main()
