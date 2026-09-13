"""Bark failure/expiry alerts, using only the Python standard library.

BARK_URL is a Secret: https://server/device-key (optional trailing slash).
Neither credentials, raw exceptions nor service response bodies are logged.
"""

import json
import os
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def bark_destination(raw):
    parsed = urlsplit(raw.strip())
    path = parsed.path.rstrip('/')
    prefix, _, key = path.rpartition('/')
    if (parsed.scheme != 'https' or not parsed.hostname or not key
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or any(c.isspace() for c in raw)):
        raise ValueError('Invalid BARK_URL')
    endpoint = urlunsplit(('https', parsed.netloc, prefix + '/push', '', ''))
    return endpoint, key


def send_bark(raw, title, body, run_url=''):
    try:
        endpoint, key = bark_destination(raw)
    except ValueError:
        print('Bark 配置无效：请将 HTTPS 服务器地址/设备密钥存入 BARK_URL Secret。')
        return False
    payload = {'device_key': key, 'title': title, 'body': body, 'group': 'GLaDOS'}
    if run_url:
        payload['url'] = run_url
    request = Request(endpoint, data=json.dumps(payload).encode('utf-8'),
                      headers={'Content-Type': 'application/json; charset=utf-8'},
                      method='POST')
    opener = build_opener(NoRedirect())
    for attempt in range(2):
        retry = False
        try:
            with opener.open(request, timeout=10) as response:
                data = json.load(response)
            if isinstance(data, dict) and data.get('code') == 200:
                print('Bark 推送成功（服务器已接受）。')
                return True
            print('Bark 服务未确认推送成功。')
            return False
        except HTTPError as error:
            print(f'Bark 返回 HTTP {error.code}。')
            retry = error.code == 429 or error.code >= 500
        except (URLError, TimeoutError, OSError):
            print('Bark 网络请求失败。')
            retry = True
        except (ValueError, TypeError):
            print('Bark 响应格式无效。')
            return False
        if not retry or attempt == 1:
            return False
        time.sleep(2)
    return False


def main():
    reason = os.environ.get('BARK_REASON', 'failure')
    raw = os.environ.get('BARK_URL', '')
    if not raw:
        print('未配置 BARK_URL Secret，无法发送 Bark 通知。')
        return 1
    server = os.environ.get('GITHUB_SERVER_URL', 'https://github.com')
    repo, run_id = os.environ.get('GITHUB_REPOSITORY', ''), os.environ.get('GITHUB_RUN_ID', '')
    run_url = f'{server}/{repo}/actions/runs/{run_id}' if repo and run_id else ''
    if reason == 'test':
        title, body = 'GLaDOS 通知测试', 'Bark 已接通。这是一条测试通知，不代表签到失败。'
    elif reason == 'renewal':
        title = 'GLaDOS 续期风险'
        body = os.environ.get('RENEWAL_WARNING', '剩余天数较少且积分不足，请检查续期。')
    else:
        title, body = 'GLaDOS 自动任务失败', '签到重试后、账户查询、兑换或工作流步骤发生错误，请打开本次 Actions 查看原因。'
    return 0 if send_bark(raw, title, body, run_url) else 1


if __name__ == '__main__':
    sys.exit(main())
