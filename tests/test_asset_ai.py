"""AI discovery and client guards; all records are synthetic."""
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.environment import claim_environment
from scripts.ai_client import Client
from scripts import source_check


class AssetAITests(unittest.TestCase):
    def test_family_client_preserves_reviewed_revision_across_connect(self):
        client = Client()
        payload = {'operation':'valuation','payload':{'item_id':'synthetic'},'expected_revision':3}
        with patch.object(client, 'request', side_effect=[{'csrf_token':'test-token','revision':9}, {'preview':True}]) as request:
            client.preview(payload, '/api/family/preview')
            self.assertEqual(request.call_args.args, ('/api/family/preview', payload))
            self.assertEqual(payload['expected_revision'], 3)
        with patch.object(client, 'request', side_effect=[{'csrf_token':'test-token','revision':9}, RuntimeError('HTTP 409')]) as request:
            with self.assertRaisesRegex(RuntimeError, '409'):
                client.write('/api/family/commands', payload, 'POST')
            self.assertEqual(request.call_count, 2)  # One connect, one write, no retry.
            self.assertEqual(payload['expected_revision'], 3)

    def test_client_rejects_missing_bad_revisions_and_unsafe_routes(self):
        client = Client()
        with patch.object(client, 'request') as request:
            for revision in (None, True, -1, '1', 1.5):
                for action in ('preview', 'write'):
                    with self.subTest(revision=revision,action=action), self.assertRaises(ValueError):
                        payload={'expected_revision':revision}
                        if action == 'preview': client.preview(payload, '/api/family/preview')
                        else: client.write('/api/family/commands', payload, 'POST')
            for path in ('/api/csv/import', '/api/restore', '/api/family/notifications'):
                with self.assertRaises(ValueError): client.write(path, {'expected_revision':1}, 'POST')
            with self.assertRaises(ValueError): client.preview({}, '/api/family/commands')
            request.assert_not_called()

    def test_capabilities_schema_and_snapshot_contract(self):
        with tempfile.TemporaryDirectory() as temp:
            data=claim_environment(Path(temp)/'synthetic','test')
            with TestClient(create_app(data,testing=True,auto_sync=False)) as c:
                cap=c.get('/api/capabilities').json()['family']
                self.assertEqual(cap['label'],'资产分析')
                contract=c.get(cap['command_schema']).json()
                variants={v['properties']['operation']['const']:v for v in contract['schema']['oneOf']}
                self.assertEqual(set(variants), set(cap['recommended_operations']))
                self.assertIn(cap['command_schema'], c.get('/api/openapi.json').json()['paths'])
                boot=c.get('/api/bootstrap').json(); headers={'X-CSRF-Token':boot['csrf_token']}
                payload={'operation':'item','payload':{'category':'cash','currency':'CNY','name':'AI合成现金','effective_on':'2026-01-01','household_amount':'123456.78'},'expected_revision':boot['revision']}
                before=c.get(cap['catalog']).json()
                preview=c.post(cap['preview'],json=payload,headers=headers)
                self.assertEqual(preview.status_code,200,preview.text)
                self.assertEqual(c.get(cap['catalog']).json(), before)
                saved=c.post(cap['write'],json=payload,headers=headers).json()
                catalog=c.get(cap['catalog']).json()
                self.assertIn(saved['result']['id'],[r['id'] for r in catalog['items']])
                self.assertEqual(c.get(cap['read']).json()['total_assets'],'123456.78')
                self.assertEqual(c.post(cap['write'],json=payload,headers=headers).status_code,409)
                self.assertEqual(c.get(cap['catalog']).json(),catalog)

    def test_skills_are_packaged_and_document_references_resolve(self):
        files={p.relative_to(source_check.ROOT).as_posix() for p in source_check.collect()}
        for skill in source_check.SKILLS:
            base=source_check.ROOT/'.agents/skills'/skill
            for part in ('SKILL.md','agents/openai.yaml'):
                self.assertIn(str((base/part).relative_to(source_check.ROOT)),files)
            for reference in re.findall(r'\]\(([^)]+)\)',(base/'SKILL.md').read_text()):
                self.assertTrue((base/reference).is_file(), reference)
            self.assertFalse(source_check.allowed(base/'private-request.json'))
            self.assertFalse(source_check.allowed(base/'private-note.md'))


if __name__ == '__main__': unittest.main()
