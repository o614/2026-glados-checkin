import io
import json
import os
import unittest
from contextlib import redirect_stdout
from unittest import mock
from urllib.error import HTTPError, URLError

import notify_bark


class BarkTests(unittest.TestCase):
    def test_destination_keeps_key_out_of_request_url(self):
        endpoint, key = notify_bark.bark_destination('https://example.com/test-key/')
        self.assertEqual(endpoint, 'https://example.com/push')
        self.assertEqual(key, 'test-key')

    def test_invalid_destinations(self):
        for raw in ['http://example.com/key', 'https://example.com/',
                    'https://user:pass@example.com/key', 'https://example.com/key?body=hello']:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                notify_bark.bark_destination(raw)

    @mock.patch('notify_bark.build_opener')
    def test_success_payload_and_logs_do_not_disclose_key(self, opener):
        opener.return_value.open.return_value.__enter__.return_value.read.return_value = b'{"code":200}'
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertTrue(notify_bark.send_bark('https://example.com/test-key', 'title', 'body'))
        request = opener.return_value.open.call_args.args[0]
        self.assertEqual(request.full_url, 'https://example.com/push')
        self.assertEqual(json.loads(request.data)['device_key'], 'test-key')
        self.assertNotIn('test-key', output.getvalue())

    @mock.patch('notify_bark.time.sleep')
    @mock.patch('notify_bark.build_opener')
    def test_network_failure_retried_without_logging_secret(self, opener, sleep):
        opener.return_value.open.side_effect = URLError('https://example.com/test-key')
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertFalse(notify_bark.send_bark('https://example.com/test-key', 'title', 'body'))
        self.assertEqual(opener.return_value.open.call_count, 2)
        self.assertNotIn('test-key', output.getvalue())
        sleep.assert_called_once_with(2)

    @mock.patch('notify_bark.build_opener')
    def test_invalid_device_is_not_retried(self, opener):
        opener.return_value.open.side_effect = HTTPError('url', 400, 'bad key', None, None)
        self.assertFalse(notify_bark.send_bark('https://example.com/test-key', 'title', 'body'))
        self.assertEqual(opener.return_value.open.call_count, 1)

    def test_redirects_are_rejected(self):
        self.assertIsNone(notify_bark.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://other.test'))

    @mock.patch('notify_bark.send_bark', return_value=True)
    def test_test_notification_is_distinct_from_failure(self, send):
        with mock.patch.dict(os.environ, {'BARK_URL': 'https://example.com/test-key', 'BARK_REASON': 'test'}, clear=True):
            self.assertEqual(notify_bark.main(), 0)
        self.assertIn('测试', send.call_args.args[1])
        self.assertIn('不代表签到失败', send.call_args.args[2])

    def test_missing_secret_is_reported(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(notify_bark.main(), 1)
