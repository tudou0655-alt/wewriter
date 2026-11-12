"""Only the settings style reaches the writer, expansion and optional style review."""
import json
import unittest
from types import SimpleNamespace

from wewriter.api import ApiError, Model
from wewriter.pipeline.composition import style_pass
from wewriter.ui import compose_style
from test_editorial_plan import ARGUMENT, SOURCES


class SettingsStyleTest(unittest.TestCase):
    def capture(self):
        requests = []
        model = Model.__new__(Model)
        def complete(system, user, **_kwargs):
            requests.append((system, json.loads(user)))
            raise ApiError('offline capture', kind='validation')
        model.complete = complete
        return model, requests

    def test_all_writing_modes_use_settings_without_card_or_fixed_voice(self):
        style = compose_style('参考原文风格', '允许长标题，用连续段落')
        sources = [dict(SOURCES[0], excerpt='虚构原文：采用专业分析语气，分段说明。'), SOURCES[1]]
        for draft_first, staged in ((False, False), (True, False), (False, True), (True, True)):
            with self.subTest(draft_first=draft_first, staged=staged):
                model, requests = self.capture()
                config = {'writing_mode': 'single_web', 'topic': '', 'target_words': 1500,
                          'draft_first_mode': draft_first, 'style': style,
                          'style_card': {'opening': '隐藏风格卡专属要求'},
                          'example': {'skeleton': '隐藏范文骨架'}}
                if staged:
                    config['editorial_plan'] = {'fact_ids': ['F1', 'F3'], 'argument': ARGUMENT,
                                                'question': '虚构问题', 'angle': '虚构主线',
                                                'domain': {'boundaries': []}}
                with self.assertRaises(ApiError):
                    model._write_once(sources, config)
                system, payload = requests[0]
                self.assertEqual(payload['user_style'], style)
                self.assertEqual(payload['sources'][0]['excerpt'], sources[0]['excerpt'])
                self.assertIn('80% 至 120%', system)
                for unwanted in ('像懂行的同行当面讲事', '开头一两句讲变化', '全文统一用一种写法',
                                 '隐藏风格卡专属要求', '隐藏范文骨架', '【风格卡】'):
                    self.assertNotIn(unwanted, system + json.dumps(payload, ensure_ascii=False))

    def test_expansion_and_style_review_receive_the_same_selected_style(self):
        config = {'style': compose_style('参考原文风格', '不要口语化'), 'target_words': 1500}
        draft = {'title': '虚构标题', 'body': '虚构正文。[S1]', 'outline': [], 'review_notes': []}
        for method in ('expand', 'style_review'):
            with self.subTest(method=method):
                model, requests = self.capture()
                with self.assertRaises(ApiError):
                    getattr(model, method)(draft, SOURCES, config)
                system, payload = requests[0]
                self.assertEqual(payload['user_style'], config['style'])
                self.assertNotIn('【风格卡】', system)
                if method == 'style_review':
                    self.assertTrue(payload['originals'])
                    self.assertNotIn('signals', payload)

    def test_fixed_readability_signals_are_not_sent_to_style_review(self):
        captured = []
        class Writer:
            def style_review(self, article, sources, config, signals):
                captured.append(signals)
                return {'scores': dict.fromkeys(('title', 'opening', 'hierarchy', 'voice',
                                                'usefulness', 'distinctness'), 5), 'rewrite': None}
        run = SimpleNamespace(writer=Writer(), config={'style': '专业简洁，允许长标题'},
                              stage=lambda *_args: None)
        article = {'title': '长标题' * 20, 'body': '虚构正文。[S1]'}
        output = style_pass(SimpleNamespace(check=lambda: None), run, article, SOURCES)
        self.assertIs(output, article)
        self.assertEqual(captured, [[]])


if __name__ == '__main__':
    unittest.main()
