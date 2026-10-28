"""T-022: the review's sentence checks and the program's re-check use one standard (offline)."""
import json
import unittest

from test_article_quality import SequenceTransport, answers, chat, package, user_json
from wewriter.api import ApiError, Model, changed_sentences, check_problems, claim_check, unsupported_attributions
from wewriter.config import defaults
from wewriter.pipeline.composition import CHECK_TABLE_HEADING, check_table, report
from wewriter.ui import review_marks

DRAFT = {'title': '考完这场试', 'outline': ['场景'], 'review_notes': [],
         'body': '有考生反映计量时间紧。[S1]\n\n成绩预计两个月内公布。[W1]'}
REWRITTEN = '据考生反映，计量科目最后半小时普遍来不及做完。'
TITLE = {'final_sentence': '标题：考完这场试', 'draft_sentence': '标题：考完这场试', 'evidence': 'S1：考生反馈',
         'verdict': '保留', 'note': ''}
KEPT = {'final_sentence': '成绩预计两个月内公布。', 'draft_sentence': '成绩预计两个月内公布。', 'evidence': 'W1：两个月',
        'verdict': '保留', 'note': ''}
FIXED = {'final_sentence': REWRITTEN, 'draft_sentence': '有考生反映计量时间紧。', 'evidence': 'S1：最后半小时普遍来不及做完',
         'verdict': '修正', 'note': '按原话补全'}


def revision(body=None, checks=(TITLE, KEPT, FIXED), **extra):
    return {**DRAFT, 'body': body or REWRITTEN + '[S1]\n\n成绩预计两个月内公布。[W1]', 'quality_notes': ['已压缩'],
            'claim_checks': list(checks), **extra}


class ReviewCheckTest(unittest.TestCase):
    def setUp(self):
        data = package()
        self.sources, self.config = data['sources'], defaults()
        self.config.update(data['config'])

    def review(self, *replies):
        transport = SequenceTransport(*[r if 'choices' in r else chat(r) for r in replies])
        model = Model('deepseek', defaults()['providers']['deepseek'], 'test-only-not-a-key', transport)
        return model.review(dict(DRAFT), self.sources, self.config), transport

    def test_a_rewrite_is_numbered_by_the_program_and_keeps_both_wordings(self):
        revised = revision()
        result, transport = self.review(revised, answers(DRAFT, revised))
        self.assertEqual(len(transport.payloads), 2)
        self.assertEqual(user_json(transport.payloads[1])['changes'], [
            {'id': 'C1', 'sentence': REWRITTEN + '[S1]', 'draft_sentence': '有考生反映计量时间紧。[S1]'}])
        line = next(n for n in result['quality_notes'] if n.startswith('逐句核对｜修正｜'))
        self.assertIn('｜初稿：有考生反映计量时间紧。[S1]', line)
        self.assertTrue(line.startswith('逐句核对｜修正｜' + REWRITTEN))

    def test_an_unanswered_rewrite_gets_one_targeted_fix(self):
        fixed = '据考生反映，计量科目时间偏紧。[S1]'
        result, transport = self.review(revision(), chat({'answers': []}),
                                        chat({'fixes': [{'id': 'C1', 'sentence': fixed, 'basis': ['S1']}]}))
        self.assertEqual(user_json(transport.payloads[2])['sentences'][0]['problem'], '没有回答')
        self.assertIn(fixed, result['body'])

    def test_still_unsupported_after_the_fix_goes_back_to_the_draft_sentence(self):
        result, transport = self.review(revision(), chat({'answers': [{'id': 'C1', 'basis': '无依据'}]}),
                                        chat({'fixes': [{'id': 'C1', 'sentence': '还是没有依据的说法', 'basis': '无依据'}]}))
        self.assertEqual(result['body'], DRAFT['body'])
        self.assertEqual(len(transport.payloads), 3)

    def test_a_rewrite_labelled_kept_is_rejected(self):
        # check_problems still reads the older model-written tables (stored reports, verification revisions).
        mislabelled = dict(FIXED, verdict='保留', note='')
        problems = check_problems(DRAFT, revision(), [TITLE, KEPT, mislabelled])
        self.assertEqual(problems, ['标为“保留”但修订稿已改写：' + REWRITTEN])

    def test_deleted_sentence_must_be_gone_and_final_wording_must_exist(self):
        deleted = {'final_sentence': '', 'draft_sentence': '成绩预计两个月内公布。', 'evidence': '无',
                   'verdict': '删除', 'note': '无依据'}
        self.assertIn('标为“删除”的句子仍在修订稿中：成绩预计两个月内公布。',
                      check_problems(DRAFT, revision(), [TITLE, KEPT, FIXED, deleted]))
        invented = dict(FIXED, final_sentence='这句话并不在修订稿里出现过。')
        self.assertTrue(any(p.startswith('final_sentence 不在修订稿中')
                            for p in check_problems(DRAFT, revision(), [TITLE, KEPT, FIXED, invented])))

    def test_changed_title_must_be_listed_with_its_new_wording(self):
        renamed = revision(title='计量科目来不及做完')
        self.assertIn('修订后的标题没有列入核对：计量科目来不及做完', check_problems(DRAFT, renamed, renamed['claim_checks']))
        listed = dict(TITLE, final_sentence='标题：计量科目来不及做完', verdict='修正', note='更直白')
        self.assertEqual(check_problems(DRAFT, renamed, [listed, KEPT, FIXED]), [])

    def test_new_format_field_rules(self):
        self.assertIsNone(claim_check(dict(FIXED, verdict='改写')))
        self.assertIsNone(claim_check(dict(FIXED, note='')))                       # a correction needs a note
        self.assertIsNone(claim_check(dict(FIXED, draft_sentence='新增')))          # a correction needs its draft
        self.assertIsNone(claim_check(dict(KEPT, final_sentence='')))
        self.assertIsNone(claim_check({'final_sentence': '还在', 'draft_sentence': '原句', 'evidence': '无',
                                       'verdict': '删除', 'note': '无依据'}))
        added = claim_check({'final_sentence': '新增一句。', 'draft_sentence': '新增', 'evidence': '无需证据',
                             'verdict': '新增', 'note': '过渡'})
        self.assertEqual((added['draft'], added['legacy']), ('', False))

    def test_older_tables_are_still_read(self):
        legacy = claim_check({'sentence': '有考生反映计量时间紧', 'evidence': 'S1：时间紧', 'action': '修正', 'note': '改为' + REWRITTEN})
        self.assertTrue(legacy['legacy'])
        table = check_table(['逐句核对｜修正｜有考生反映计量时间紧｜证据：S1：时间紧｜改为' + REWRITTEN])
        self.assertIn('| 修正 | 有考生反映计量时间紧 | （见说明） |', table)

    def test_two_column_table_and_red_marks_from_it(self):
        lines = ['逐句核对｜保留｜申报截止 11 月 30 日｜证据：无',
                 '逐句核对｜保留｜一、政策变化与申报要点｜证据：无需证据',
                 '逐句核对｜修正｜今年补贴提高到 50 万元｜证据：S1：50 万元｜按通知改｜初稿：今年补贴翻倍',
                 '逐句核对｜新增｜往年为 30 万元｜证据：W1：30 万元｜补充对比',
                 '逐句核对｜删除｜全国企业都能申报｜证据：无｜无依据']
        table = check_table(lines)
        self.assertTrue(table.startswith(CHECK_TABLE_HEADING))
        for row in ('| 修正 | 今年补贴翻倍 | 今年补贴提高到 50 万元 | S1：50 万元 | 按通知改 |',
                    '| 新增 | （新增） | 往年为 30 万元 | W1：30 万元 | 补充对比 |',
                    '| 删除 | 全国企业都能申报 | （已删除） | 无 | 无依据 |'):
            self.assertIn(row, table)
        config = {**defaults(), 'selection_mode': 'recent'}
        source = {'source_id': 'S1', 'title': '原文', 'account_name': '号', 'url': 'https://mp.weixin.qq.com/s/a',
                  'analysis': [{'summary': '摘要', 'facts': [], 'uncertainties': []}]}
        text = report(config, [source], {'outline': [], 'review_notes': []}, lines + ['已压缩结尾'], None, False)
        self.assertIn('## AI 二审\n- 已压缩结尾', text)
        self.assertIn(CHECK_TABLE_HEADING, text)
        body = '## 一、政策变化与申报要点\n\n申报截止 11 月 30 日。[S1]\n\n今年补贴提高到 50 万元。[S1]'
        marks, _ = review_marks('标题', body, text)
        self.assertEqual(marks, {1: ['二审未找到证据：申报截止 11 月 30 日']})   # 无需证据 is not a finding


class ProgramNumberingTest(unittest.TestCase):
    """Shaped like the rejected 稿件-10 revisions of 2026-10-27 (fictional wording): the reviewer
    softened a count in the title, a heading, a lead-in and a list item; DeepSeek could not copy
    them back verbatim, so the program now numbers them itself."""

    DRAFT = {'title': '示例设备验收前，先对照这五条', 'outline': [], 'review_notes': [], 'body': (
        '示例企业最近都在盯一件事：资料齐不齐。[S1]\n\n### 二、五条核查重点，每条都要留凭证\n\n'
        '下面是材料中反复出现的五条核查线。[S1]\n\n老设备不能只是挪到仓库角落继续使用。[S1]\n\n'
        '1. 按上述五条逻辑先行自查，补齐三类基础材料；\n2. 联系主管部门确认本批次安排。[W1]')}

    def revised(self):
        body = (self.DRAFT['body'].replace('二、五条', '二、几条').replace('反复出现的五条', '提到的几条')
                .replace('上述五条', '上述几条').replace('示例企业最近都在盯一件事：资料齐不齐。[S1]\n\n', ''))
        return {**self.DRAFT, 'title': '示例设备验收前，先对照这几条', 'body': body}

    def test_changes_and_deletions_are_numbered_in_order(self):
        changes, deletions = changed_sentences(self.DRAFT, self.revised())
        self.assertEqual([(c['id'], c['kind']) for c in changes],
                         [('C1', 'title'), ('C2', 'sentence'), ('C3', 'sentence'), ('C4', 'sentence')])
        self.assertEqual(changes[1], {'kind': 'sentence', 'id': 'C2', 'text': '二、几条核查重点，每条都要留凭证',
                                      'draft': '二、五条核查重点，每条都要留凭证'})
        self.assertEqual(changes[3]['draft'], '按上述五条逻辑先行自查，补齐三类基础材料；')
        self.assertEqual(deletions, [{'id': 'D1', 'draft': '示例企业最近都在盯一件事：资料齐不齐。[S1]'}])

    def test_call_count(self):
        data = package()
        config = {**defaults(), **data['config']}
        revised = {**self.revised(), 'quality_notes': []}

        def run(*replies):
            transport = SequenceTransport(chat(revised), *replies)
            model = Model('deepseek', defaults()['providers']['deepseek'], 'k', transport)
            return model.review(dict(self.DRAFT), data['sources'], config), len(transport.payloads)

        ok = {'answers': [{'id': f'C{n}', 'basis': '无需依据' if n < 3 else ['S1']} for n in range(1, 5)]}
        result, calls = run(chat(ok))
        self.assertEqual(calls, 2)                         # revision + basis
        self.assertEqual(result['title'], '示例设备验收前，先对照这几条')
        lines = [n for n in result['quality_notes'] if n.startswith('逐句核对｜')]
        self.assertEqual(len(lines), 5)                    # four changes and the deletion
        self.assertTrue(lines[-1].startswith('逐句核对｜删除｜示例企业最近都在盯一件事'))
        missing = {'answers': ok['answers'][:3]}
        result, calls = run(chat(missing), chat({'fixes': []}))
        self.assertEqual(calls, 3)                         # + one targeted fix
        self.assertIn('按上述五条逻辑先行自查', result['body'])        # C4 went back to the draft
        self.assertIn('二、几条核查重点', result['body'])                  # the answered ones stay


class FalsePositiveTest(unittest.TestCase):
    def test_warning_against_spreading_is_not_a_spread_claim(self):
        sources = package()['sources']
        for sentence in ('不应将392万元成交及供应商信息当作确定事实传播。', '在官方公告前，避免把这条消息转发给客户。'):
            self.assertEqual(unsupported_attributions(sentence, sources), [], sentence)
        self.assertEqual(unsupported_attributions('这篇文章在备考圈流传，说缺考率很高。', sources),
                         ['这篇文章在备考圈流传，说缺考率很高。'])

    def test_quote_notes_about_removed_quotes_are_not_carried(self):
        data = package()
        sources, config = data['sources'], defaults()
        config.update(data['config'])
        draft = dict(DRAFT, review_notes=['引号内容未能在资料中逐字核对，发布前请核实或改为转述：“大家都说基本靠蒙”',
                                          '核对截止日期'])
        transport = SequenceTransport(chat(revision()), answers(DRAFT, revision()))
        model = Model('deepseek', defaults()['providers']['deepseek'], 'test-only-not-a-key', transport)
        notes = model.review(draft, sources, config)['review_notes']
        self.assertNotIn('引号内容未能在资料中逐字核对，发布前请核实或改为转述：“大家都说基本靠蒙”', notes)
        self.assertIn('核对截止日期', notes)
        quoted = '有考生说：“最后半小时基本靠蒙”。'
        body = quoted + '[S1]\n\n成绩预计两个月内公布。[W1]'
        listed = dict(FIXED, final_sentence=quoted)
        transport = SequenceTransport(chat(revision(body=body)), answers(DRAFT, revision(body=body)))
        model = Model('deepseek', defaults()['providers']['deepseek'], 'test-only-not-a-key', transport)
        notes = model.review(draft, sources, config)['review_notes']
        self.assertTrue(any('基本靠蒙' in n and '逐字核对' in n for n in notes))   # still there: recomputed


if __name__ == '__main__':
    unittest.main()
