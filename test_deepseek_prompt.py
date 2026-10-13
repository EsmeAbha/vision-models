"""Exercise actual route code with worker/thread dependencies mocked (no GPU)."""
import ast
from pathlib import Path
import queue
import threading
import types
import unittest
from unittest.mock import Mock


class PromptTests(unittest.TestCase):
    def route(self, prompt):
        tree = ast.parse(Path(__file__).with_name('vision_server.py').read_text(encoding='utf-8'))
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'api_run')
        fn.decorator_list = []
        fn.args.args[0].annotation = None
        request = types.SimpleNamespace(file_id='file', model='deepseek_ocr', prompt=prompt, doc_type='')
        worker = Mock()
        env = {'_state_lock': threading.Lock(), '_uploads': {'file': {'name':'test.png'}},
               'MODEL_META': {'deepseek_ocr': {'steps': []}}, 'uuid': types.SimpleNamespace(uuid4=lambda: types.SimpleNamespace(hex='run')),
               'queue':queue, 'time':types.SimpleNamespace(time=lambda:0), '_runs':{},
               '_evict_runs':Mock(), '_do_run':Mock(), 'threading':types.SimpleNamespace(Thread=worker),
               'vision':types.SimpleNamespace(DEFAULT_PROMPTS={'deepseek_ocr':'<image>\nConvert the document to markdown.'})}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), 'route', 'exec'), env)
        env['api_run'](request)
        return worker.call_args.kwargs['args'][2].prompt

    def test_blank_uses_default(self):
        self.assertEqual(self.route('  '), '<image>\nConvert the document to markdown.')

    def test_custom_instruction_reaches_worker_unchanged(self):
        self.assertEqual(self.route('Read only the invoice number.'), 'Read only the invoice number.')


if __name__ == '__main__': unittest.main()
