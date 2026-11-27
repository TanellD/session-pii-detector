"""Регрессии отдельной ревизии адаптера на синтетических примерах."""
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'src'),str(ROOT)]
from session_pii.adapter_revision import revise
from session_pii.components import DetectionResult
from session_pii.models import Span,Message
from session_pii.pipeline import Pipeline


class AdapterRevisionTests(unittest.TestCase):
    def span(self,text,value,kind):
        start=text.index(value)
        return Span(type=kind,start=start,end=start+len(value),score=.9)

    def test_invalid_email_and_age_do_not_remove_valid_contact(self):
        text='Личная почта owner@example.invalid, возраст словами: сорок лет.'
        base=DetectionResult((self.span(text,'Личная почта','email'),
            self.span(text,'owner@example.invalid','email'),self.span(text,'сорок лет','age')))
        result,rejected=revise(text,base)
        self.assertEqual([(e.type,text[e.start:e.end]) for e in result.spans], [('email','owner@example.invalid')])
        self.assertEqual(len(rejected),2)
        Pipeline._validate(Message(message_id=1,text=text),result.spans,(None,))

    def test_full_address_abbreviations_survive(self):
        text='Адрес: г. Пробоград, ул. Макетная, д. 8, кв. 4.'
        result,_=revise(text,DetectionResult((self.span(text,'Пробоград','geography'),)))
        self.assertEqual([(e.type,text[e.start:e.end]) for e in result.spans],
            [('address','г. Пробоград, ул. Макетная, д. 8, кв. 4')])

    def test_document_context_preserves_checksum_and_completeness(self):
        text='Паспорт: серия и номер 0000 000000. ИНН физического лица: 000000000000.'
        result,_=revise(text,DetectionResult())
        self.assertEqual([e.type for e in result.spans],['passport','individual_inn'])
        bad='ИНН физического лица: 000000000001. Паспорт: серия и номер 0000 0000000.'
        self.assertFalse(revise(bad,DetectionResult())[0].spans)

    def test_person_boundaries_and_separate_context_source(self):
        text='Меня зовут Тестомир, фамилия Пробов.'
        original=self.span(text,'Тестомир','person')
        result,_=revise(text,DetectionResult((original,)))
        self.assertEqual([(e.type,text[e.start:e.end]) for e in result.spans],
            [('first_name','Тестомир'),('last_name','Пробов')])
        self.assertTrue(all(e.score is None and e.provenance[0].source=='regex' for e in result.spans))
        self.assertEqual(original.start,text.index('Тестомир'))

    def test_unresolved_person_is_retained_and_pronoun_filtered(self):
        text='Кодовое обозначение Тестомир. Мы оба согласны.'
        result,_=revise(text,DetectionResult((self.span(text,'Тестомир','person'),self.span(text,'Мы оба','person'))))
        self.assertEqual([(e.type,text[e.start:e.end]) for e in result.spans],[('person','Тестомир')])


if __name__=='__main__':
    unittest.main()
