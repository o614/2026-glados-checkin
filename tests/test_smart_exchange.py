import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import requests
import checkin


def client(points=100, days=13):
    g = checkin.GLaDOS('test-cookie')
    g.points, g.left_days = str(points), str(days)
    g.available_plans = {key: dict(value) for key, value in checkin.EXCHANGE_PLANS.items()}
    g.checkin = mock.Mock(return_value={'code': 0, 'message': 'Checkin! Got points'})
    g.get_status = mock.Mock(return_value=True)
    g.get_points = mock.Mock(return_value=True)
    g.exchange = mock.Mock(return_value={'code': 0})
    return g


class SmartExchangeTests(unittest.TestCase):
    def test_urgent_balance_boundaries(self):
        for points, expected in [(9, None), (99, None), (100, 'plan100'),
                                 (199, 'plan100'), (200, 'plan200'),
                                 (499, 'plan200'), (500, 'plan500')]:
            with self.subTest(points=points):
                self.assertEqual(checkin.smart_exchange_plan(client(points)), expected)

    def test_buffer_boundary_and_expired_account(self):
        for days, expected in [(14.01, None), (14, 'plan100'), (0, 'plan100'),
                               (-0.5, 'plan100')]:
            with self.subTest(days=days):
                self.assertEqual(checkin.smart_exchange_plan(client(100, days)), expected)

    def test_best_value_can_be_redeemed_early(self):
        self.assertEqual(checkin.smart_exchange_plan(client(500, 60)), 'plan500')
        self.assertIsNone(checkin.smart_exchange_plan(client(200, 60)))

    def test_current_server_prices_control_decision(self):
        g = client(100, 5)
        g.available_plans['plan100']['points'] = 120
        self.assertIsNone(checkin.smart_exchange_plan(g))
        g.points = '120'
        checkin.auto_exchange(g, 'smart')
        g.exchange.assert_called_once_with('plan100')

    def test_invalid_data_never_spends_points(self):
        for field, value in [('points', '?'), ('points', '-1'), ('points', 'nan'),
                             ('left_days', 'inf'), ('available_plans', {}),
                             ('available_plans', {'other': {'points': 1, 'days': 100}})]:
            with self.subTest(field=field, value=value):
                g = client(500, 2)
                setattr(g, field, value)
                self.assertTrue(checkin.auto_exchange(g, 'smart').startswith('⚠️'))
                g.exchange.assert_not_called()

    def test_invalid_server_plans_are_ignored(self):
        plans = {'plan100': {'points': 0, 'days': 10},
                 'plan200': {'points': 200, 'days': float('nan')},
                 'plan500': {'points': 500, 'days': 100}}
        self.assertEqual(list(checkin.valid_exchange_plans(plans)), ['plan500'])

    def test_exchange_timeout_is_not_replayed_or_logged_with_cookie(self):
        g = checkin.GLaDOS('test-sensitive-cookie')
        output = io.StringIO()
        with mock.patch('checkin.requests.post', side_effect=requests.Timeout('test-sensitive-cookie')) as post:
            with redirect_stdout(output):
                self.assertIsNone(g.exchange('plan100'))
        self.assertEqual(post.call_count, 1)
        self.assertFalse(post.call_args.kwargs['allow_redirects'])
        self.assertNotIn('test-sensitive-cookie', output.getvalue())

    def test_success_with_failed_refresh_reports_uncertainty(self):
        g = client()
        g.get_status.return_value = False
        self.assertTrue(checkin.auto_exchange(g, 'smart').startswith('⚠️'))
        g.exchange.assert_called_once()

    def test_expiry_warning_only_when_unaffordable(self):
        self.assertTrue(checkin.renewal_warning(client(99, 7)))
        self.assertFalse(checkin.renewal_warning(client(100, 7)))
        self.assertFalse(checkin.renewal_warning(client(9, 7.01)))


class MainFlowTests(unittest.TestCase):
    def run_main(self, g, output_file=None):
        env = {'GLADOS_COOKIE': 'test-cookie', 'EXCHANGE_PLAN': 'smart',
               'CHECKIN_RETRY_DELAY_SECONDS': '0'}
        if output_file:
            env['GITHUB_OUTPUT'] = output_file
        with mock.patch.dict(os.environ, env, clear=True), mock.patch('checkin.GLaDOS', return_value=g):
            return checkin.main()

    def test_failed_checkin_retries_three_times_and_never_exchanges(self):
        g = client(500)
        g.checkin.return_value = {'code': -2, 'message': 'Unauthorized'}
        self.assertEqual(self.run_main(g), 1)
        self.assertEqual(g.checkin.call_count, 3)
        g.exchange.assert_not_called()

    def test_failed_balance_query_causes_failure_and_no_exchange(self):
        g = client(500)
        g.get_points.return_value = False
        self.assertEqual(self.run_main(g), 1)
        g.exchange.assert_not_called()

    def test_failed_exchange_causes_nonzero_exit(self):
        g = client()
        g.exchange.return_value = {'code': 1, 'message': 'denied'}
        self.assertEqual(self.run_main(g), 1)

    def test_normal_wait_succeeds_without_spending(self):
        g = client(9, 13)
        self.assertEqual(self.run_main(g), 0)
        g.exchange.assert_not_called()

    def test_warning_output_contains_no_cookie(self):
        with tempfile.TemporaryDirectory() as directory:
            output = str(Path(directory) / 'outputs')
            self.assertEqual(self.run_main(client(99, 3), output), 0)
            text = Path(output).read_text(encoding='utf-8')
            self.assertTrue(text.startswith('renewal_warning='))
            self.assertNotIn('test-cookie', text)
            self.assertIn('99', text)
