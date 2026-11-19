"""Run tests without using application data or changing .env.

python backend/run_tests.py                   # No database, browser or provider
python backend/run_tests.py --integration     # Disposable MySQL database; mocked provider
python backend/run_tests.py --browser         # Chrome UI tests with controlled API replies
python backend/run_tests.py --live-esignet    # Disposable database and running eSignet services

Flags may be combined. Integration runs use the MySQL server credentials in .env
or the process environment, but create and remove their own randomly named database.
The account needs CREATE/DROP DATABASE permissions. Live runs use the configured
eSignet issuer/client/key; run setup_esignet.py first. No real emails are sent.
"""
import argparse
import base64
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import unittest

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / 'backend' / 'tests'
INTEGRATION = {'test_exam_session_integration.py', 'test_esignet_integration.py'}
LIVE = 'test_esignet_live.py'


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--integration', action='store_true', help='Run database-backed exam and identity tests')
    parser.add_argument('--browser', action='store_true', help='Run Chrome UI tests (no real backend or email)')
    parser.add_argument('--live-esignet', action='store_true', help='Test real OTP/consent with the running local identity provider')
    args = parser.parse_args()
    load_dotenv(ROOT / '.env')
    os.environ.update({
        'JWT_SECRET': secrets.token_urlsafe(48),
        'RECORDING_KEY': base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
        'SEB_REQUIRED': 'true', 'SEB_CONFIG_KEY': secrets.token_hex(32),
        'COOKIE_SECURE': 'false', 'ESIGNET_DEMO_ENABLED': 'true',
        'MONITORING_AUTO_START': 'false', 'PILOT_MAX_SESSIONS': '0',
        'RUN_ESIGNET_LIVE': '1' if args.live_esignet else '0',
    })
    # Unit/browser runs must not inherit the development database either.
    name = 'assessbridge_test_' + secrets.token_hex(6)
    os.environ['DB_NAME'] = name
    os.environ['ASSESSBRIDGE_TEST_DATABASE'] = name
    sys.path[:0] = [str(ROOT / 'backend'), str(TESTS)]
    database_tests = args.integration or args.live_esignet
    created = False
    server = None
    result_code = 1
    with tempfile.TemporaryDirectory(prefix='assessbridge-tests-') as temporary:
        os.environ['RECORDINGS_DIR'] = str(Path(temporary) / 'recordings')
        try:
            if database_tests:
                import mysql.connector
                server = dict(host=os.getenv('DB_HOST', '127.0.0.1'),
                              port=int(os.getenv('DB_PORT', '3306')),
                              user=os.getenv('DB_USER', 'root'), password=os.getenv('DB_PASSWORD', ''),
                              connection_timeout=5)
                with mysql.connector.connect(**server) as db:
                    cursor = db.cursor()
                    # No IF NOT EXISTS: never reuse an existing database.
                    cursor.execute(f'CREATE DATABASE `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci')
                    created = True
                    cursor.close()
                print('Created disposable test database: ' + name, flush=True)
                subprocess.run([sys.executable, str(ROOT / 'backend' / 'init_db.py')],
                               cwd=ROOT, check=True)
            suite = unittest.TestSuite()
            for path in sorted(TESTS.glob('test_*.py')):
                if path.name in INTEGRATION and not args.integration:
                    continue
                if path.name == LIVE and not args.live_esignet:
                    continue
                if path.name.endswith('_browser.py') and not args.browser:
                    continue
                suite.addTests(unittest.defaultTestLoader.discover(str(TESTS), pattern=path.name))
            result = unittest.TextTestRunner(verbosity=2).run(suite)
            result_code = 0 if result.wasSuccessful() else 1
        finally:
            if created:
                assert os.environ['DB_NAME'] == name
                with mysql.connector.connect(**server) as db:
                    cursor = db.cursor()
                    cursor.execute(f'DROP DATABASE `{name}`')
                    cursor.close()
                print('Removed disposable test database: ' + name, flush=True)
    return result_code


if __name__ == '__main__':
    sys.exit(main())
