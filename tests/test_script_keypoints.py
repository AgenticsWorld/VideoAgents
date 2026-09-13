"""Run directly with python3 -m unittest discover -s tests -p test_script_keypoints.py."""
import ast
import json
import re
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from modules import script_keypoints as kp


class KeypointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        (self.base / 'story/episodes/ep01').mkdir(parents=True)

    def add(self, **kwargs):
        return kp.mutate(self.base, 'ep01', {'action': 'add', 'kind': 'plot', 'parts': [
            {'context': '他握住🔑，推开门。', 'quote': '🔑', 'start': 3, 'end': 5, 'scene': 'S01'}], **kwargs})

    def test_roundtrip_and_confirmation(self):
        item = self.add()[0]
        self.assertEqual(kp.load(self.base, 'ep01')[0]['parts'][0]['quote'], '🔑')
        self.assertIn(item['id'], kp.prompt(self.base))
        with self.assertRaisesRegex(ValueError, '用户确认'):
            kp.mutate(self.base, 'ep01', {'action': 'remove', 'id': item['id']})
        self.assertEqual(kp.coverage(self.base, 'ep01'), [item['id']])
        kp.mutate(self.base, 'ep01', {'action': 'remove', 'id': item['id'], 'confirmed': True})
        self.assertEqual(kp.coverage(self.base, 'ep01'), [])
        self.assertTrue(kp.load(self.base, 'ep01')[0]['removed'])
        self.assertEqual(kp.prompt(self.base), '')

    def test_invalid_batch_is_atomic(self):
        with self.assertRaises(ValueError):
            self.add(parts=[{'context': 'abc', 'quote': 'z', 'start': 0, 'end': 1, 'scene': ''}])
        self.assertEqual(kp.load(self.base, 'ep01'), [])
        with self.assertRaises(ValueError):
            kp.mutate(self.base, '../ep01', {})

    def test_concurrent_additions(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _: self.add(), range(12)))
        self.assertEqual(len(kp.load(self.base, 'ep01')), 12)

    def test_coverage_requires_per_shot_evidence(self):
        item = self.add()[0]
        folder = self.base / 'directing/ep01'
        folder.mkdir(parents=True)
        path = folder / 'shot_list.json'
        path.write_text(json.dumps({'keypoint_refs': [{'id': item['id'], 'evidence': '动作'}],
                                    'shots': [{'keypoint_refs': [{'id': item['id'], 'evidence': ''}]}]}))
        self.assertEqual(kp.coverage(self.base, 'ep01'), [item['id']])
        path.write_text(json.dumps({'shots': [{'id': 'sh001', 'keypoint_refs': [
            {'id': item['id'], 'evidence': '特写：手中的钥匙插入门锁。'}]}]}))
        self.assertEqual(kp.coverage(self.base, 'ep01'), [])
        self.assertEqual(kp.coverage(self.base, 'ep01', 'storyboard.json'), [item['id']])

    def test_runtime_postcheck(self):
        # Isolate this hook from the runtime's optional SDK imports and global state.
        source = Path('services/runtime/core.py').read_text()
        fn = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == '_keypoints_postcheck')
        scope = {'_proj_base': lambda _: self.base, 're': re}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), '<runtime hook>', 'exec'), scope)
        self.add()
        for agent in ('07-directing/storyboard', '07-directing/shot-planning'):
            run = {'status': 'done', 'agent': agent, 'project': 'demo', 'message': '完成 ep01'}
            scope['_keypoints_postcheck'](run)
            self.assertEqual(run['status'], 'error')
            self.assertIn('关键点', run['error'])
        run = {'status': 'done', 'agent': '07-directing/director', 'project': 'demo'}
        scope['_keypoints_postcheck'](run)
        self.assertEqual(run['status'], 'done')


if __name__ == '__main__':
    unittest.main()
