"""Log redaction tests. Every credential below is obviously fake."""
import io
import logging
import unittest

from wewriter.logs import MASK, _Redact, redact

FAKE_SK = 'sk-FAKE0000TEST0000DUMMY0000'
FAKE_BEARER = 'FAKE-bearer-token-0000.test'
FAKE_API = 'FAKE_api_key_0000'
FAKE_SKILL = 'FAKE_skill_key_0000'
FAKE_SKEY = 'FAKEskey0000'
FAKE_RT = 'FAKE%2Frt0000'
FAKE_VID = '00000000'

LEAK_CASES = [
    ('bearer header', 'Authorization: Bearer %s' % FAKE_BEARER, FAKE_BEARER),
    ('bearer lower', 'headers bearer %s sent' % FAKE_BEARER, FAKE_BEARER),
    ('authorization dict', "{'Authorization': 'Bearer %s'}" % FAKE_BEARER, FAKE_BEARER),
    ('authorization no scheme', 'authorization=%s' % FAKE_API, FAKE_API),
    ('bare sk key', 'model rejected key %s' % FAKE_SK, FAKE_SK),
    ('sk in quotes', 'key="%s"' % FAKE_SK, FAKE_SK),
    ('sess key', 'token sess-FAKE0000TEST0000DUMMY', 'sess-FAKE0000TEST0000DUMMY'),
    ('url api_key', 'GET https://api.example.test/v1?api_key=%s&page=2' % FAKE_API, FAKE_API),
    ('url key param', 'GET https://api.example.test/v1?key=%s' % FAKE_API, FAKE_API),
    ('json api_key', '{"api_key": "%s", "model": "demo"}' % FAKE_API, FAKE_API),
    ('json apiKey', '{"apiKey":"%s"}' % FAKE_API, FAKE_API),
    ('x-api-key header', 'X-Api-Key: %s' % FAKE_API, FAKE_API),
    ('kv skill_key', 'skill_key=%s retry=1' % FAKE_SKILL, FAKE_SKILL),
    ('json skillKey', '{"skillKey": "%s"}' % FAKE_SKILL, FAKE_SKILL),
    ('access token', '{"accessToken": "%s"}' % FAKE_SKEY, FAKE_SKEY),
    ('cookie wr_skey', 'Cookie: wr_vid=%s; wr_skey=%s; wr_rt=%s' % (FAKE_VID, FAKE_SKEY, FAKE_RT),
     FAKE_SKEY),
    ('cookie wr_rt', 'Cookie: wr_vid=%s; wr_skey=%s; wr_rt=%s' % (FAKE_VID, FAKE_SKEY, FAKE_RT),
     FAKE_RT),
    ('cookie wr_vid', 'Cookie: wr_vid=%s; wr_skey=%s' % (FAKE_VID, FAKE_SKEY), FAKE_VID),
]

KEEP_CASES = [
    ('http status', 'HTTP 401 Unauthorized'),
    ('request id', 'request_id=req-FAKE-0001 status=429'),
    ('error type', 'ApiError: TimeoutError while calling provider'),
    ('file name', r'写入 C:\data\exports\article-2026.docx 失败'),
    ('task id', 'task-1234567890abcdef1234 finished'),
    ('risk word', 'risk-assessment-report-for-quarter ready'),
    ('max tokens', 'max_tokens=4096 temperature=0.7'),
    ('monkey field', 'monkey=banana1234'),
    ('bearer prose', 'the bearer of good news'),
    ('key not configured', 'skill_key 未配置，跳过官方 Skill'),
    ('short sk word', 'scikit sk-learn installed'),
]


def make_logger():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter('%(levelname)s %(name)s: %(message)s'))
    handler.addFilter(_Redact())
    log = logging.getLogger('wewriter.test_logs.%d' % id(stream))
    log.propagate = False
    log.setLevel(logging.DEBUG)
    log.addHandler(handler)
    return log, stream


class RedactTextTest(unittest.TestCase):
    def test_secrets_are_masked(self):
        for name, text, secret in LEAK_CASES:
            with self.subTest(name):
                cleaned = redact(text)
                self.assertNotIn(secret, cleaned)
                self.assertIn(MASK, cleaned)

    def test_non_secrets_are_untouched(self):
        for name, text in KEEP_CASES:
            with self.subTest(name):
                self.assertEqual(redact(text), text)

    def test_context_around_secret_is_kept(self):
        cleaned = redact('HTTP 401 request_id=req-FAKE-0002 api_key=%s file=a.json' % FAKE_API)
        self.assertEqual(cleaned, 'HTTP 401 request_id=req-FAKE-0002 api_key=%s file=a.json' % MASK)

    def test_redaction_is_idempotent(self):
        once = redact('Authorization: Bearer %s' % FAKE_BEARER)
        self.assertEqual(redact(once), once)

    def test_unexpected_input_does_not_raise(self):
        self.assertEqual(redact(None), 'None')
        self.assertEqual(redact(401), '401')


class RedactFilterTest(unittest.TestCase):
    def test_parameterized_message(self):
        log, stream = make_logger()
        log.warning('任务 %s 失败 [%s]：%s', 'run-1', 'HTTP 401', 'api_key=%s' % FAKE_API)
        output = stream.getvalue()
        self.assertNotIn(FAKE_API, output)
        self.assertIn('任务 run-1 失败 [HTTP 401]：api_key=' + MASK, output)

    def test_exception_message_and_traceback(self):
        log, stream = make_logger()
        try:
            raise ValueError('upstream rejected Bearer %s and %s' % (FAKE_BEARER, FAKE_SK))
        except ValueError as exc:
            log.warning('请求失败：%s', exc)
            log.exception('内部异常')
        output = stream.getvalue()
        self.assertNotIn(FAKE_BEARER, output)
        self.assertNotIn(FAKE_SK, output)
        self.assertIn('Traceback', output)
        self.assertIn('ValueError', output)
        self.assertIn('test_logs.py', output)

    def test_bad_format_args_still_logged(self):
        log, stream = make_logger()
        log.error('需要两个参数 %s %s token=%s', 'only-one')
        output = stream.getvalue()
        self.assertIn('需要两个参数', output)
        self.assertIn('日志参数格式化失败', output)

    def test_non_string_message(self):
        log, stream = make_logger()
        log.info({'api_key': FAKE_API, 'status': 200})
        output = stream.getvalue()
        self.assertNotIn(FAKE_API, output)
        self.assertIn("'status': 200", output)

    def test_record_always_passes(self):
        record = logging.LogRecord('x', logging.INFO, __file__, 1, 'cookie wr_skey=%s', (FAKE_SKEY,), None)
        self.assertTrue(_Redact().filter(record))
        self.assertNotIn(FAKE_SKEY, record.getMessage())


if __name__ == '__main__':
    unittest.main()
