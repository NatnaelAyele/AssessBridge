"""Clone setup is repeatable, keeps secrets private and preserves OTP semantics."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import setup_esignet as setup
from app import config,esignet
from app.seb_config import build_settings


class EsignetSetupTests(unittest.TestCase):
    def test_repeatable_bootstrap_and_protected_otp_input(self):
        with tempfile.TemporaryDirectory(prefix='esignet-setup-') as folder:
            root=Path(folder)
            with patch.object(setup,'ROOT',root),patch.object(setup,'RUNTIME',root/'var/esignet'):
                identities=setup.prepare('http://127.0.0.1:3000','http://127.0.0.1:8000')
                runtime=root/'var/esignet'
                private=(runtime/'client-private.pem').read_bytes()
                settings=(runtime/'runtime.env').read_bytes()
                setup.prepare('http://127.0.0.1:3000','http://127.0.0.1:8000')
                self.assertEqual((runtime/'client-private.pem').read_bytes(),private)
                self.assertEqual((runtime/'runtime.env').read_bytes(),settings)
                flow=(runtime/'flow.yaml').read_text()
                self.assertIn('type: OTP_INPUT',flow)
                self.assertNotIn('- *captcha_box',flow)
                self.assertNotIn('- *login_id_uin',flow)
                self.assertIn('Demo OTP (111111)',flow)
                self.assertEqual([item['fullName'] for item in identities],['Daniel Bekele','Samuel Mensah','Grace Njeri'])
                sql=(runtime/'bootstrap.sql').read_text()
                for identity in identities:
                    self.assertIn(identity['individualId'],sql)
                    self.assertIn(identity['fullName'],sql)
                self.assertNotIn('Siddharth',(runtime/'init.sql').read_text())
                self.assertNotIn('PRIVATE KEY',sql)
                self.assertIn('ESIGNET_DEMO_ENABLED',(root/'.env').read_text())

    def test_seb_origin_is_allowed_only_for_demo_exams(self):
        with patch.object(config,'ESIGNET_DEMO_ENABLED',True),patch.object(config,'ESIGNET_ISSUER','http://127.0.0.1:3000'):
            ordinary=build_settings('http://127.0.0.1:8000','http://127.0.0.1:8000/exam-entry','test')
            demo=build_settings('http://127.0.0.1:8000','http://127.0.0.1:8000/exam-entry','test',esignet.POLICY)
            self.assertEqual(len(ordinary['URLFilterRules']),1)
            self.assertEqual(len(demo['URLFilterRules']),2)
            self.assertIn('127.0.0.1:3000/*',[row['expression'] for row in demo['URLFilterRules']])
