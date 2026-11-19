import smtplib
import unittest
from email.message import EmailMessage
from unittest.mock import MagicMock, patch

from app.mail import MailDeliveryError, MailSettings, _send_message, delivery_failure, send_calendar_invitation
from datetime import datetime, timedelta, timezone
from uuid import uuid4


class MailDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.settings = MailSettings('smtp.example.com', 465, 'ssl', 'sender@example.com',
                                     'private-app-password', 'sender@example.com')
        self.message = EmailMessage()
        self.message['From'] = self.settings.sender
        self.message['To'] = 'candidate@example.com'
        self.message.set_content('Test invitation')

    def test_local_settings_override_stale_process_values_and_reload(self):
        local = {'SMTP_HOST': 'smtp.example.com', 'SMTP_PORT': '465', 'SMTP_SECURITY': 'ssl',
                 'SMTP_USERNAME': 'sender@example.com', 'SMTP_PASSWORD': 'private-app-password',
                 'SMTP_SENDER': 'sender@example.com'}
        with patch('app.mail.dotenv_values', return_value=local), patch.dict('os.environ', {'SMTP_PORT': '587', 'SMTP_SECURITY': 'starttls'}):
            self.assertEqual(MailSettings.from_environment().port, 465)
            self.assertEqual(MailSettings.from_environment().security, 'ssl')
            local['SMTP_PORT'], local['SMTP_SECURITY'] = '587', 'starttls'
            self.assertEqual(MailSettings.from_environment().port, 587)
            self.assertEqual(MailSettings.from_environment().security, 'starttls')

    def test_environment_used_when_no_local_settings(self):
        with patch('app.mail.dotenv_values', return_value={}), patch.dict('os.environ', {
            'SMTP_HOST': 'smtp.example.com', 'SMTP_PORT': '465', 'SMTP_SECURITY': 'ssl',
            'SMTP_USERNAME': 'sender@example.com', 'SMTP_PASSWORD': 'password', 'SMTP_SENDER': 'sender@example.com'}):
            self.assertEqual(MailSettings.from_environment().port, 465)

    def test_ssl_sends_once(self):
        smtp = MagicMock()
        with patch('app.mail.smtplib.SMTP_SSL', return_value=smtp):
            _send_message(self.settings, self.message)
        smtp.login.assert_called_once_with(self.settings.username, self.settings.password)
        smtp.send_message.assert_called_once_with(self.message)
        smtp.starttls.assert_not_called()
        smtp.quit.assert_called_once()

    def test_starttls_still_supported(self):
        settings = MailSettings('smtp.example.com', 587, 'starttls', 'sender@example.com', 'secret', 'sender@example.com')
        smtp = MagicMock()
        with patch('app.mail.smtplib.SMTP', return_value=smtp):
            _send_message(settings, self.message)
        smtp.starttls.assert_called_once()
        self.assertEqual(smtp.ehlo.call_count, 2)
        smtp.send_message.assert_called_once()

    def test_failure_reasons_are_specific_and_do_not_expose_provider_secrets(self):
        cases = [
            ('login', smtplib.SMTPAuthenticationError(535, b'PRIVATE server details'), 'authentication_failed'),
            ('send_message', smtplib.SMTPRecipientsRefused({'candidate@example.com': (550, b'PRIVATE details')}), 'recipient_rejected'),
            ('send_message', smtplib.SMTPSenderRefused(550, b'PRIVATE details', 'sender@example.com'), 'sender_rejected'),
            ('send_message', smtplib.SMTPDataError(554, b'PRIVATE details'), 'message_rejected'),
            ('send_message', TimeoutError('PRIVATE details'), 'delivery_unconfirmed'),
        ]
        for method, error, code in cases:
            with self.subTest(code=code):
                smtp = MagicMock()
                getattr(smtp, method).side_effect = error
                with patch('app.mail.smtplib.SMTP_SSL', return_value=smtp), self.assertLogs('app.mail', level='WARNING') as logs:
                    with self.assertRaises(MailDeliveryError) as caught:
                        _send_message(self.settings, self.message)
                self.assertEqual(delivery_failure(caught.exception)['code'],
                                 'recipient_rejected' if code == 'recipient_rejected' else 'delivery_failed')
                self.assertNotIn('PRIVATE', str(caught.exception))
                self.assertNotIn('private-app-password', '\n'.join(logs.output))
                self.assertNotIn('PRIVATE', '\n'.join(logs.output))

    def test_connection_timeout_is_not_a_recipient_error(self):
        with patch('app.mail.smtplib.SMTP_SSL', side_effect=TimeoutError()), self.assertLogs('app.mail', level='WARNING'):
            with self.assertRaises(MailDeliveryError) as caught:
                _send_message(self.settings, self.message)
        self.assertEqual(caught.exception.code, 'connection_failed')

    def test_quit_failure_does_not_cancel_accepted_email_or_retry(self):
        smtp = MagicMock()
        smtp.quit.side_effect = smtplib.SMTPServerDisconnected()
        with patch('app.mail.smtplib.SMTP_SSL', return_value=smtp):
            _send_message(self.settings, self.message)
        smtp.send_message.assert_called_once()
        smtp.close.assert_called_once()

    def test_invitation_message_can_be_serialized_with_all_parts(self):
        start = datetime.now(timezone.utc) + timedelta(hours=1)
        with patch('app.mail._send_message') as send:
            send_calendar_invitation(settings=self.settings, assessment_id=1, invitation_id=1,
                title='Law - domestic dispute', duration_minutes=30, start=start,
                end=start + timedelta(hours=1), candidate_name='Candidate',
                candidate_email='candidate@example.com', username='ABCDE', password='exam1234')
        message = send.call_args.args[1]
        self.assertIn(b'Law - domestic dispute', message.as_bytes())
        self.assertIn('text/calendar', [part.get_content_type() for part in message.walk()])

    def test_failed_batch_rolls_back_and_reports_delivery_reason(self):
        from app.invitations import SendInvitationsRequest, send_all_invitations
        start = datetime.now(timezone.utc) + timedelta(hours=1)
        payload = SendInvitationsRequest(request_key=uuid4(), title='Law - domestic dispute',
            duration_minutes=30, starts_at=start, ends_at=start + timedelta(hours=1),
            questions=[{'type': 'written', 'prompt': 'Question', 'key': 'Answer'}],
            candidates=[{'name': 'Candidate', 'email': 'candidate@example.com'}])
        connection, cursor = MagicMock(), MagicMock()
        connection.cursor.return_value = cursor
        cursor.fetchone.side_effect = [None, None]
        cursor.lastrowid = 1
        cursor.fetchall.return_value = [{'id': 2, 'candidate_name': 'Candidate',
            'candidate_email': 'candidate@example.com', 'candidate_user_id': None, 'exam_username': 'ABCDE'}]
        context = MagicMock()
        context.__enter__.return_value = connection
        with patch('app.invitations.database', return_value=context), \
                patch('app.invitations.SEB_REQUIRED', False), \
                patch('app.invitations.MailSettings.from_environment', return_value=self.settings), \
                patch('app.invitations.hash_password', return_value='hash'), \
                patch('app.retention.snapshot'), \
                patch('app.invitations.send_calendar_invitation', side_effect=MailDeliveryError('connection_failed', 'Could not connect to the mail server.')):
            result = send_all_invitations(payload, 1)
        connection.rollback.assert_called_once()
        connection.commit.assert_not_called()
        self.assertFalse(result['saved'])
        self.assertIsNone(result['assessment_id'])
        self.assertEqual(result['delivery_error']['code'], 'delivery_failed')
        self.assertEqual(result['failed_email'], 'candidate@example.com')

    def test_public_response_does_not_forward_exception_details(self):
        for exception in (MailDeliveryError('connection_failed', 'SMTP private-host:465 SMTP_SENDER secret'),
                          OSError('private-host:465 secret')):
            response = delivery_failure(exception)
            self.assertEqual(response, {'code': 'delivery_failed',
                'message': 'Email delivery is temporarily unavailable. Try again later.'})


if __name__ == '__main__':
    unittest.main()
