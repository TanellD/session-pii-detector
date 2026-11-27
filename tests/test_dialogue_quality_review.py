"""Частичные ответы аудитора и проверка цитат без принятия выдуманных свидетельств."""
import json
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'tools'),str(ROOT/'src'),str(ROOT)]
from review_dialogue_quality import parse


def response(rows):
    return dict(choices=[dict(finish_reason='stop',message=dict(content=json.dumps(dict(reviews=rows))))])


def review(rid, issues=None):
    return dict(review_id=rid,verdict='revise' if issues else 'pass',estimated_difficulty='easy',reason='Review',issues=issues or [])


class DialogueQualityReviewTests(unittest.TestCase):
    batch=[dict(review_id='q001',messages=[dict(id='m1',text='First message.'),dict(id='m2',text='Second message.')]),
           dict(review_id='q002',messages=[dict(id='m1',text='Other dialogue.')])]

    def test_partial_response_retains_valid_review_and_reports_missing(self):
        accepted,rejected=parse(response([review('q001')]),self.batch)
        self.assertEqual([r['review_id'] for r in accepted],['q001'])
        self.assertEqual(rejected,[dict(review_id='q002',error='Missing review')])

    def test_unique_quote_repairs_only_message_index_and_records_provenance(self):
        issue=dict(category='grammar',severity='minor',message_id=-1,quote='Second message.',reason='Check phrasing')
        accepted,_=parse(response([review('q001',[issue])]),self.batch)
        self.assertEqual(accepted[0]['issues'][0]['message_id'],2)
        self.assertEqual(accepted[0]['validation_repairs'][0]['basis'],'unique_exact_quote_match')
        self.assertEqual(issue['message_id'],-1)

    def test_invented_quote_is_rejected(self):
        issue=dict(category='logic',severity='major',message_id=1,quote='Not present.',reason='Claim')
        accepted,rejected=parse(response([review('q001',[issue])]),self.batch)
        self.assertEqual(accepted,[])
        self.assertTrue(any('quote' in row['error'] for row in rejected))

    def test_ambiguous_quote_does_not_get_arbitrary_message_index(self):
        batch=[dict(review_id='q001',messages=[dict(id='m1',text='Same.'),dict(id='m2',text='Same.')])]
        issue=dict(category='grammar',severity='minor',message_id=-1,quote='Same.',reason='Claim')
        accepted,rejected=parse(response([review('q001',[issue])]),batch)
        self.assertEqual(accepted,[])
        self.assertEqual(len(rejected),1)


if __name__=='__main__':
    unittest.main()
