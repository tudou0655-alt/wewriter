"""T-062: the review may not turn the article into a retelling of its material."""
import unittest

from wewriter.api import ARTICLE_VOICE, undo_retelling


class UndoRetellingTest(unittest.TestCase):
    def test_retelling_added_by_review_is_left_for_fact_review(self):
        draft = {'title': '广东小巨人奖补：每家最高120万元',
                 'body': '珠三角每家100万元，粤东西北每家120万元。[S1]\n\n资金要建专账。[S1]'}
        revision = {'title': '行业材料提及广东小巨人奖补金额',
                    'body': '行业材料称，珠三角每家100万元，粤东西北每家120万元。[S1]\n\n资金要建专账。[S1]'}
        fixed, notes = undo_retelling(draft, revision)
        self.assertEqual(fixed['title'], revision['title'])
        self.assertIn('珠三角每家100万元，粤东西北每家120万元。[S1]', fixed['body'])
        self.assertIn('行业材料', fixed['body'])
        self.assertEqual(len(notes), 2)

    def test_fact_corrections_without_retelling_are_kept(self):
        draft = {'title': '标题', 'body': '珠三角每家120万元。[S1]'}
        revision = {'title': '标题', 'body': '珠三角每家不超过100万元。[W1]'}
        fixed, notes = undo_retelling(draft, revision)
        self.assertEqual(fixed['body'], '珠三角每家不超过100万元。[W1]')
        self.assertEqual(notes, [])

    def test_writer_is_told_to_write_a_new_article(self):
        self.assertIn('不是对资料的转述或点评', ARTICLE_VOICE)


if __name__ == '__main__':
    unittest.main()
