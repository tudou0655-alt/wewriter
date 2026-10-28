"""T-018: writing and review receive their answer as a server-sent event stream (offline)."""
import json
import threading
import unittest
from unittest.mock import Mock

from wewriter.api import ApiError, Cancelled, Model, Transport

DEEPSEEK = {'model': 'deepseek-test', 'base_url': 'https://api.deepseek.example'}
OPENAI = {'model': 'gpt-test', 'base_url': 'https://api.openai.example/v1'}
ARTICLE = {'title': '标题', 'outline': ['a'], 'body': '正文。[S1]', 'review_notes': []}


class FakeResponse:
    """A streamed HTTP response: readline() returns the queued lines; an exception in the
    queue is raised at that point, as a socket timeout would be."""

    def __init__(self, lines):
        self.lines = list(lines)
        self.closed = False

    def readline(self, limit=-1):
        if not self.lines:
            return b''
        item = self.lines.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def read(self, limit=-1):
        rest, self.lines = b''.join(x for x in self.lines if isinstance(x, bytes)), []
        return rest

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True


def sse(*events, done=True):
    lines = []
    for event in events:
        lines += [b'data: ' + json.dumps(event, ensure_ascii=False).encode('utf-8') + b'\n', b'\n']
    return lines + ([b'data: [DONE]\n', b'\n'] if done else [])


def chat_chunks(text, finish='stop', size=4):
    pieces = [text[i:i + size] for i in range(0, len(text), size)]
    events = [{'choices': [{'delta': {'role': 'assistant'}, 'finish_reason': None}]}]
    events += [{'choices': [{'delta': {'content': p}, 'finish_reason': None}]} for p in pieces]
    events.append({'choices': [{'delta': {}, 'finish_reason': finish}]})
    events.append({'choices': [], 'usage': {'prompt_tokens': 10, 'completion_tokens': 20}})
    return events


def transport(*responses):
    t = Transport(threading.Event())
    t.opener = Mock()
    t.opener.open.side_effect = list(responses)
    return t


def payload_of(t, call=0):
    return json.loads(t.opener.open.call_args_list[call][0][0].data)


class ChatStreamTest(unittest.TestCase):
    def test_chunks_are_joined_and_stream_options_sent(self):
        text = json.dumps(ARTICLE, ensure_ascii=False)
        t = transport(FakeResponse([b': keep-alive\n', b'\n'] + sse(*chat_chunks(text))))
        result = Model('deepseek', DEEPSEEK, 'k', t).complete('系统', '用户', stream=True)
        self.assertEqual(result, ARTICLE)
        sent = payload_of(t)
        self.assertTrue(sent['stream'])
        self.assertEqual(sent['stream_options'], {'include_usage': True})
        request = t.opener.open.call_args_list[0][0][0]
        self.assertEqual(request.get_header('Accept'), 'text/event-stream')
        self.assertEqual(t.opener.open.call_args_list[0][1]['timeout'], 60)

    def test_write_and_review_stream(self):
        text = json.dumps(ARTICLE, ensure_ascii=False)
        t = transport(FakeResponse(sse(*chat_chunks(text))))
        draft = Model('deepseek', DEEPSEEK, 'k', t).write([{'source_id': 'S1', 'title': '原文'}],
                                                         {'topic': '', 'style': '', 'target_words': 1500})
        self.assertEqual(draft['body'], '正文。[S1]')
        self.assertTrue(payload_of(t)['stream'])

    def test_length_finish_is_truncation(self):
        t = transport(FakeResponse(sse(*chat_chunks('{"title": "半', finish='length'))))
        with self.assertRaisesRegex(ApiError, '长度上限'):
            Model('deepseek', DEEPSEEK, 'k', t).complete('s', 'u', stream=True)

    def test_done_without_finish_reason_is_abnormal_end(self):
        events = chat_chunks('{"ok": true}')[:-2]
        t = transport(FakeResponse(sse(*events)))
        with self.assertRaisesRegex(ApiError, '未正常结束'):
            Model('deepseek', DEEPSEEK, 'k', t).complete('s', 'u', stream=True)

    def test_connection_closed_mid_stream_is_a_network_error(self):
        events = chat_chunks('{"ok": true}')[:-2]
        t = transport(FakeResponse(sse(*events, done=False)))
        with self.assertRaises(ApiError) as caught:
            Model('deepseek', DEEPSEEK, 'k', t).complete('s', 'u', stream=True)
        self.assertEqual(caught.exception.kind, 'network')

    def test_error_object_in_stream(self):
        t = transport(FakeResponse(sse({'error': {'message': 'quota'}})))
        with self.assertRaisesRegex(ApiError, '服务返回错误对象'):
            Model('deepseek', DEEPSEEK, 'k', t).complete('s', 'u', stream=True)


class IdleAndTotalTimeoutTest(unittest.TestCase):
    def test_idle_timeout_fails_without_resending(self):
        lines = sse(*chat_chunks('{"ok": true}'))[:4] + [TimeoutError('timed out')]
        t = transport(FakeResponse(lines), FakeResponse(sse(*chat_chunks('{"ok": true}'))))
        with self.assertRaises(ApiError) as caught:
            Model('deepseek', DEEPSEEK, 'k', t).complete('s', 'u', stream=True)
        self.assertEqual(caught.exception.kind, 'network')
        self.assertIn('连续 60 秒没有收到数据', str(caught.exception))
        self.assertEqual(t.opener.open.call_count, 1)

    def test_total_limit(self):
        t = transport(FakeResponse(sse(*chat_chunks('{"ok": true}'))))
        with self.assertRaises(ApiError) as caught:
            list(t.stream('https://api.example/x', 'k', {}, total_timeout=-1))
        self.assertEqual(caught.exception.kind, 'network')
        self.assertIn('总时长上限', str(caught.exception))

    def test_stop_task_cancels_the_stream(self):
        t = transport(FakeResponse(sse(*chat_chunks('{"ok": true}'))))
        events = t.stream('https://api.example/x', 'k', {})
        next(events)
        t.cancel.set()
        with self.assertRaises(Cancelled):
            list(events)


class ResponsesStreamTest(unittest.TestCase):
    def test_deltas_assembled_when_final_event_has_no_text(self):
        text = json.dumps({'ok': True})
        events = [{'type': 'response.created', 'response': {'status': 'in_progress'}}]
        events += [{'type': 'response.output_text.delta', 'delta': text[i:i + 3]} for i in range(0, len(text), 3)]
        events.append({'type': 'response.completed', 'response': {'status': 'completed', 'output': [],
                                                                  'usage': {'output_tokens': 5}}})
        t = transport(FakeResponse(sse(*events, done=False)))
        self.assertEqual(Model('openai', OPENAI, 'k', t).complete('s', 'u', stream=True), {'ok': True})
        self.assertTrue(payload_of(t)['stream'])
        self.assertFalse(payload_of(t)['store'])

    def test_final_output_is_used(self):
        final = {'status': 'completed', 'output': [{'type': 'message', 'content': [
            {'type': 'output_text', 'text': '{"ok": 1}'}]}]}
        t = transport(FakeResponse(sse({'type': 'response.output_text.delta', 'delta': 'ignored'},
                                       {'type': 'response.completed', 'response': final}, done=False)))
        self.assertEqual(Model('openai', OPENAI, 'k', t).complete('s', 'u', stream=True), {'ok': 1})

    def test_incomplete_is_truncation(self):
        final = {'status': 'incomplete', 'incomplete_details': {'reason': 'max_output_tokens'}, 'output': []}
        t = transport(FakeResponse(sse({'type': 'response.output_text.delta', 'delta': '{"a'},
                                       {'type': 'response.incomplete', 'response': final}, done=False)))
        with self.assertRaisesRegex(ApiError, '未完整返回'):
            Model('openai', OPENAI, 'k', t).complete('s', 'u', stream=True)

    def test_error_event(self):
        t = transport(FakeResponse(sse({'type': 'error', 'message': 'bad'}, done=False)))
        with self.assertRaisesRegex(ApiError, '服务返回错误对象'):
            Model('openai', OPENAI, 'k', t).complete('s', 'u', stream=True)


class FallbackTest(unittest.TestCase):
    def test_provider_switch_turns_streaming_off(self):
        class Both:
            def __init__(self):
                self.streamed = False
                self.timeouts = []

            def stream(self, *args, **kwargs):
                self.streamed = True
                raise AssertionError('must not stream')

            def request(self, url, key, payload=None, *, timeout=100):
                self.timeouts.append(timeout)
                assert 'stream' not in payload
                return {'choices': [{'finish_reason': 'stop', 'message': {'content': '{"ok": true}'}}]}
        t = Both()
        model = Model('deepseek', {**DEEPSEEK, 'stream': False}, 'k', t)
        self.assertEqual(model.complete('s', 'u', timeout=240, stream=True), {'ok': True})
        self.assertFalse(t.streamed)
        self.assertEqual(t.timeouts, [240])

    def test_server_ignoring_stream_returns_plain_json(self):
        body = json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': '{"ok": true}'}}]})
        t = transport(FakeResponse([body[:10].encode(), body[10:].encode()]))
        self.assertEqual(Model('deepseek', DEEPSEEK, 'k', t).complete('s', 'u', stream=True), {'ok': True})

    def test_summaries_stay_unstreamed(self):
        body = json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': '{"ok": true}'}}]})
        t = transport(FakeResponse([body.encode()]))
        Model('deepseek', DEEPSEEK, 'k', t).complete('s', 'u')
        self.assertNotIn('stream', payload_of(t))


if __name__ == '__main__':
    unittest.main()
