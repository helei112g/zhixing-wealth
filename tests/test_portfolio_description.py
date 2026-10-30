"""Synthetic portfolio introductions: round trips, compatibility and conflicts."""
import tempfile
import unittest

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.common import DomainError, json_dumps


class PortfolioDescriptionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app(self.temp.name, testing=True, auto_sync=False)
        self.client = TestClient(self.app)
        self.client.__enter__()
        boot = self.client.get('/api/bootstrap').json()
        self.headers = {'x-csrf-token': boot['csrf_token']}
        self.payload = {'name': '合成介绍组合', 'planned_amount': '10000', 'start_date': '2026-01-01'}

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def create(self, **extra):
        response = self.client.post('/api/portfolios', json={**self.payload, **extra}, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def edit(self, portfolio, **extra):
        return self.client.put('/api/portfolios/' + portfolio['id'],
                               json={**self.payload, **extra}, headers=self.headers)

    def test_plain_text_roundtrip_and_financial_isolation(self):
        text = '背景：为长期目标积累资金。\n\n策略：分批投入，定期复核。\n<b>保留为纯文字</b>'
        p = self.create(description=text)
        other = self.create(name='另一合成组合')
        full = self.client.get('/api/portfolios/' + p['id'] + '/dashboard').json()
        summary = self.client.get('/api/portfolios/' + p['id'] + '/dashboard?view=summary').json()
        self.assertEqual(full['portfolio']['description'], text)
        self.assertEqual(summary['portfolio']['description'], text)
        response = self.edit(p, description='更新背景\n更新策略')
        self.assertEqual(response.status_code, 200, response.text)
        updated = self.client.get('/api/portfolios/' + p['id'] + '/dashboard').json()
        self.assertEqual(updated['capital_summary'], full['capital_summary'])
        self.assertEqual(updated['curve'], full['curve'])
        portfolios = {v['id']: v for v in self.client.get('/api/bootstrap').json()['portfolios']}
        self.assertEqual(portfolios[p['id']]['description'], '更新背景\n更新策略')
        self.assertEqual(portfolios[other['id']]['description'], '')

    def test_omitted_and_null_preserve_but_empty_clears(self):
        p = self.create(description='原背景与策略')
        for changes in ({'name': '修改名称'}, {'description': None}):
            response = self.edit(p, **changes)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['description'], '原背景与策略')
        self.assertEqual(self.edit(p, description='').json()['description'], '')

    def test_legacy_read_does_not_rewrite_and_can_add_introduction(self):
        p = self.create()
        p.pop('description')
        store = self.app.state.store
        with store.transaction() as db:
            db.execute('UPDATE portfolios SET data=? WHERE id=?', (json_dumps(p), p['id']))
        before = store.state()
        self.client.get('/api/bootstrap')
        self.client.get('/api/portfolios/' + p['id'] + '/dashboard')
        self.assertEqual(store.state(), before)
        self.assertEqual(self.edit(p).json()['description'], '')
        self.assertEqual(self.edit(p, description='补充策略').json()['description'], '补充策略')

    def test_invalid_description_and_stale_revision_do_not_write(self):
        p = self.create(description='原介绍')
        store = self.app.state.store
        before = store.state()
        for value in ('文' * 10001, ['非纯文字']):
            response = self.edit(p, description=value)
            self.assertEqual(response.status_code, 422)
            with self.assertRaises(DomainError):
                self.app.state.service.save_portfolio({**self.payload, 'description': value}, p['id'])
            self.assertEqual(store.state(), before)
        stale = before['revision']
        self.assertEqual(self.edit(p, description='已核对的新介绍', expected_revision=stale).status_code, 200)
        before = store.state()
        response = self.edit(p, description='过期草稿', expected_revision=stale)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(store.state(), before)
        self.assertEqual(before['portfolios'][0]['description'], '已核对的新介绍')


if __name__ == '__main__':
    unittest.main()
