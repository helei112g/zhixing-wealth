"""Recent AI contracts preserve canonical values and exact update scope."""
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.app import create_app
from backend.environment import claim_environment
from backend.research.api import UpdateRequest
from scripts.source_check import SKILLS


class AICoverageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        data = claim_environment(Path(self.temp.name) / 'synthetic', 'test')
        self.app = create_app(data, testing=True, auto_sync=False)
        self.client = TestClient(self.app).__enter__()
        self.boot = self.client.get('/api/bootstrap').json()
        self.headers = {'X-CSRF-Token': self.boot['csrf_token']}

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def test_discovery_tracks_packaged_skills_and_independent_revisions(self):
        cap = self.client.get('/api/capabilities').json()
        self.assertEqual(set(cap['skills']), set(SKILLS))
        self.assertEqual(cap['family']['comparison']['history_curve'], 'previous')
        domains = cap['concurrency']['domains']
        self.assertEqual([domains[k]['field'] for k in ('ledger', 'research', 'settings')],
                         ['expected_revision', 'expected_research_revision', 'expected_settings_revision'])
        for domain in domains.values():
            self.assertEqual(self.client.get(domain['read']).status_code, 200)
        self.assertTrue(cap['research']['scenario']['read_only'])
        self.assertEqual(cap['portfolios']['description']['max_length'], 10000)

    def test_openapi_exposes_choices_metrics_and_discriminated_update_resources(self):
        schema = self.client.get('/api/openapi.json').json()
        paths, models = schema['paths'], schema['components']['schemas']
        params = {p['name']: p['schema'] for p in paths['/api/research/indices/{index_id}']['get']['parameters']}
        self.assertEqual(params['basis']['enum'], ['price', 'total_return'])
        self.assertEqual(params['method']['enum'], ['overall', 'median', 'arithmetic'])
        self.assertEqual(params['window']['enum'], [3, 5, 10])
        self.assertIn('409', paths['/api/research/indices/{index_id}/scenarios']['post']['responses'])
        self.assertIn('ScenarioResult', models)
        self.assertIn('reference_coverage', models['ScenarioResult']['properties'])
        self.assertIn('source', models['ValuationPreviewResponse']['properties'])
        self.assertIn('holding_profit', models['CapitalSummary']['properties'])
        self.assertIn('account_available_cash', models['CapitalSummary']['properties'])
        self.assertIn('unallocated_target_amount', models['PlanFunding']['properties'])
        resources = models['UpdateRequest']['properties']['resources']['anyOf'][0]['items']
        self.assertEqual(resources['discriminator']['propertyName'], 'scope')
        self.assertEqual(len(resources['oneOf']), 6)
        self.assertNotIn('/api/tags', paths)

    def test_dashboard_schema_preserves_full_and_summary_values_including_unknowns(self):
        service = self.app.state.service
        portfolio = service.save_portfolio(dict(name='合成契约组合', planned_amount='10000', start_date='2024-01-01'))
        pid = portfolio['id']
        for with_holding in (False, True):
            if with_holding:
                service.save_event(dict(portfolio_id=pid, kind='BUY', date='2024-01-02',
                    instrument=dict(market='US', code='AICT', name='合成缺汇率标的'),
                    quantity='1', gross_amount='10', fx_rate='7'))
            for view in ('full', 'summary'):
                expected = service.dashboard(pid, view=view)
                response = self.client.get(f'/api/portfolios/{pid}/dashboard?view={view}')
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json(), expected)
        self.assertIsNone(response.json()['capital_summary']['holding_profit'])

    def test_update_resources_keep_null_and_omitted_fields_without_expanding_scope(self):
        resources = [dict(scope='quotes', instrument_id=None),
                     dict(scope='fx', currency='HKD', full=True),
                     dict(scope='index_prices', target='synthetic', module='total_return'),
                     dict(scope='memberships', target='synthetic', module='historical_memberships'),
                     dict(scope='valuations', target='synthetic', module='stock_valuations'),
                     dict(scope='financials', target='synthetic', module='historical_financials')]
        for resource in resources:
            body = dict(scopes=[resource['scope']], resources=[resource], request_id='synthetic-scope')
            self.assertEqual(UpdateRequest.model_validate(body).model_dump(exclude_unset=True), body)
        for resource in [dict(scope='quotes'), dict(scope='fx', currency='CNY'),
                         dict(scope='fx', currency='HKD', full='true'),
                         dict(scope='index_prices', target='synthetic', module='financials'),
                         dict(scope='financials', target='synthetic', module='financials', full=True)]:
            with self.subTest(resource=resource), self.assertRaises(ValidationError):
                UpdateRequest.model_validate(dict(resources=[resource], request_id='synthetic-invalid'))


if __name__ == '__main__':
    unittest.main()
