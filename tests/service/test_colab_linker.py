"""Проверки сохранения и продолжения локального линкера без GPU и API."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'tools'), str(ROOT/'src'), str(ROOT)]
from colab_linker import run_sessions, read, write, local_messages
from export_colab_linker import notebook


class ColabLinkerTests(unittest.TestCase):
    def test_local_prompt_keeps_input_and_distinguishes_new_from_unknown(self):
        body=dict(messages=[dict(role='system',content='base'),dict(role='user',content='input')],
            response_format=dict(json_schema=dict(schema=dict(type='object'))))
        messages=local_messages(body)
        self.assertEqual(body['messages'][0]['content'],'base')
        self.assertEqual(messages[1],body['messages'][1])
        self.assertIn('создай new:N',messages[0]['content'])
        self.assertIn('О КОТОРОМ',messages[0]['content'])

    def test_resume_and_failed_contract_preserve_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root/'inference.json'
            write(source, dict(sessions=[dict(session_id='synthetic-colab-test', messages=[
                dict(id='m1', text='Тестимир', candidates=[dict(type='first_name',start=0,end=8)])])]))
            identity = dict(model_id='synthetic-test-model', max_new_tokens=128)
            calls = []
            def invalid(body):
                calls.append(body)
                return dict(choices=[dict(finish_reason='length',message=dict(content=''))])
            output = root/'results'
            state = run_sessions(source, output, invalid, identity)
            self.assertEqual(state['sessions'][-1]['status'], 'failed')
            run_sessions(source, output, invalid, identity)
            self.assertEqual(len(calls), 1)
            def valid(body):
                calls.append(body)
                return dict(choices=[dict(finish_reason='stop',message=dict(content=json.dumps(dict(
                    assignments=[dict(candidate_id='c1',owners=['new:1'],service_contact=False)]))))])
            state = run_sessions(source, output, valid, identity, retry_failed=True)
            self.assertEqual(state['sessions'][-1]['status'], 'complete')
            run_sessions(source, output, invalid, identity)
            self.assertEqual(len(calls), 2)
            self.assertTrue((output/'synthetic-colab-test/attempt-01/call-01-response.json').exists())
            self.assertTrue((output/'synthetic-colab-test/attempt-02/prediction.json').exists())
            with self.assertRaises(ValueError):
                run_sessions(source, output, valid, dict(identity, max_new_tokens=256))

    def test_notebook_python_cells_compile_and_no_outputs(self):
        doc = notebook()
        self.assertEqual(doc['nbformat'], 4)
        for cell in doc['cells']:
            if cell['cell_type'] == 'code':
                compile(cell['source'], cell['id'], 'exec')
                self.assertEqual(cell['outputs'], [])


if __name__ == '__main__':
    unittest.main()
