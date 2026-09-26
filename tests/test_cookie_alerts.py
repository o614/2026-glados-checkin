import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import checkin
import notify_bark


class CookieAlertTests(unittest.TestCase):
    def test_login_errors_are_distinct_from_other_failures(self):
        for message in ['没有权限', '未登录', 'Cookie expired', 'Unauthorized!']:
            with self.subTest(message=message):
                self.assertTrue(checkin.is_cookie_error({'message': message}))
        for result in [None, {'code': 403}, {'message': 'Forbidden'},
                       {'message': 'Network Error'}, {'message': '积分不足'},
                       {'message': 'Checkin Repeats! Please Try Tomorrow'}]:
            with self.subTest(result=result):
                self.assertFalse(checkin.is_cookie_error(result))

    @mock.patch('checkin.time.sleep')
    def test_cookie_failure_stops_retrying(self, sleep):
        g = mock.Mock()
        g.checkin.return_value = {'code': -2, 'message': '没有权限'}
        _, success = checkin.checkin_with_retry(g)
        self.assertFalse(success)
        g.checkin.assert_called_once()
        sleep.assert_not_called()

    @mock.patch('checkin.requests.get')
    def test_status_query_login_rejection_is_recorded(self, get):
        get.return_value.status_code = 200
        get.return_value.json.return_value = {'message': '没有权限'}
        g = checkin.GLaDOS('test-cookie')
        self.assertFalse(g.get_status())
        self.assertTrue(g.cookie_rejected)

    @mock.patch('checkin.requests.get')
    def test_generic_http_403_is_not_labeled_cookie_expiry(self, get):
        get.return_value.status_code = 403
        g = checkin.GLaDOS('test-cookie')
        self.assertFalse(g.get_status())
        self.assertFalse(g.cookie_rejected)

    def test_full_new_and_legacy_cookie_fields_are_preserved(self):
        value = 'koa:sess=old; koa:sess.sig=oldsig; gld:sess=new; gld:sess.sig=newsig'
        with mock.patch.dict(os.environ, {'GLADOS_COOKIE': value}, clear=True):
            self.assertEqual(checkin.get_cookies(), [value])

    @mock.patch('notify_bark.send_bark', return_value=True)
    def test_rejection_output_routes_to_cookie_specific_bark(self, send):
        g = checkin.GLaDOS('test-cookie')
        g.checkin = mock.Mock(return_value={'message': '没有权限'})
        g.get_status = mock.Mock(return_value=False)
        g.get_points = mock.Mock(return_value=False)
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'output'
            env = {'GLADOS_COOKIE': 'test-cookie', 'EXCHANGE_PLAN': 'smart',
                   'GITHUB_OUTPUT': str(output)}
            with mock.patch.dict(os.environ, env, clear=True), mock.patch('checkin.GLaDOS', return_value=g):
                self.assertEqual(checkin.main(), 1)
            self.assertEqual(output.read_text(encoding='utf-8'), 'failure_reason=cookie\n')
            reason = output.read_text(encoding='utf-8').strip().split('=', 1)[1]
            with mock.patch.dict(os.environ, {'BARK_REASON': reason, 'BARK_URL': 'https://example.com/test-key'}, clear=True):
                self.assertEqual(notify_bark.main(), 0)
        self.assertIn('Cookie', send.call_args.args[1])
        self.assertIn('GLADOS_COOKIE', send.call_args.args[2])
        self.assertNotIn('test-cookie', send.call_args.args[2])

    def test_missing_secret_has_separate_reason(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'output'
            with mock.patch.dict(os.environ, {'GITHUB_OUTPUT': str(output)}, clear=True):
                self.assertEqual(checkin.main(), 1)
            self.assertEqual(output.read_text(encoding='utf-8'), 'failure_reason=cookie_missing\n')

    @mock.patch('notify_bark.send_bark', return_value=True)
    def test_unknown_failure_keeps_generic_notification(self, send):
        with mock.patch.dict(os.environ, {'BARK_REASON': 'failure', 'BARK_URL': 'https://example.com/test-key'}, clear=True):
            self.assertEqual(notify_bark.main(), 0)
        self.assertNotIn('Cookie', send.call_args.args[1])
