"""Prepare and start the isolated, fictional eSignet authentication environment."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import time
from urllib.parse import urlsplit
from urllib.request import urlopen

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from dotenv import dotenv_values, set_key
from app.esignet_keys import public_jwk

ROOT = Path(__file__).resolve().parents[1]
INFRA = ROOT / 'infra' / 'esignet'
RUNTIME = ROOT / 'var' / 'esignet'


def docker_command():
    if shutil.which('docker'):
        return ['docker'], lambda p: str(p)
    if os.name == 'nt' and shutil.which('wsl'):
        result = subprocess.run(['wsl', '-d', 'Ubuntu', '-u', 'root', '--', 'docker', 'version', '--format', '{{.Server.Version}}'], capture_output=True)
        if result.returncode == 0:
            def linux_path(path):
                path = Path(path).resolve()
                return '/mnt/' + path.drive[0].lower() + path.as_posix()[2:]
            return ['wsl', '-d', 'Ubuntu', '-u', 'root', '--', 'docker'], linux_path
    raise SystemExit('Install Docker Engine with Compose, or Docker Desktop, and start it before running this command.')


def keep_wsl_running(command):
    if command[0] != 'wsl':
        return
    # WSL may shut down when the last Windows-launched Linux process exits,
    # even while systemd has background services. Keep a hidden session open.
    marker=RUNTIME/'wsl-keeper.pid'
    if marker.exists():
        try:
            pid=int(marker.read_text())
            # Verify the command as well as the PID: Windows can reuse a saved
            # process number after reboot for an unrelated application.
            result=subprocess.run(['powershell','-NoProfile','-Command',f"Get-CimInstance Win32_Process -Filter 'ProcessId={pid}' | Select-Object ExecutablePath,CommandLine | ConvertTo-Json -Compress"],
                                  capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=10)
            process=json.loads(result.stdout or '{}') or {}
            if Path(process.get('ExecutablePath') or '').name.lower()=='wsl.exe' and 'sleep infinity' in (process.get('CommandLine') or ''):
                return
        except (ValueError,OSError,subprocess.TimeoutExpired):
            pass
    keeper=subprocess.Popen(command[:-1]+['sleep','infinity'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    marker.write_text(str(keeper.pid))


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def prepare(issuer, app_url):
    for value in (issuer, app_url):
        url = urlsplit(value)
        if url.scheme not in ('http', 'https') or not url.hostname or url.query or url.fragment or url.username or url.path not in ('', '/'):
            raise SystemExit('Use absolute origin URLs without a path, username, query or fragment.')
        if url.scheme == 'http' and url.hostname not in ('127.0.0.1', 'localhost', '::1'):
            raise SystemExit('Non-local demo addresses require HTTPS.')
    RUNTIME.mkdir(parents=True, exist_ok=True)
    key_path = RUNTIME / 'client-private.pem'
    if not key_path.exists():
        private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        with key_path.open('xb') as file:
            file.write(private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        key_path.chmod(0o600)
    private = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    jwk = public_jwk(private)
    env_path = RUNTIME / 'runtime.env'
    old = dotenv_values(env_path) if env_path.exists() else {}
    url = urlsplit(issuer)
    runtime = {'POSTGRES_PASSWORD': old.get('POSTGRES_PASSWORD') or secrets.token_urlsafe(32),
               'KEYSTORE_PASSWORD': old.get('KEYSTORE_PASSWORD') or secrets.token_urlsafe(24),
               'ESIGNET_ISSUER': issuer, 'UI_HOST': url.hostname,
               'UI_PORT': str(url.port or (443 if url.scheme == 'https' else 80)), 'UI_SCHEME': url.scheme,
               'ESIGNET_BIND_PORT': str((url.port or 80) if url.scheme=='http' and url.hostname in ('127.0.0.1','localhost','::1') else 3000)}
    env_path.write_text(''.join(f'{key}={value}\n' for key, value in runtime.items()), encoding='utf-8')
    env_path.chmod(0o600)
    upstream = (INFRA / 'upstream' / 'init.sql').read_text(encoding='utf-8')
    # Remove only the supplied sample identity INSERT, preserving the schema.
    upstream = upstream.split('INSERT INTO mockidentitysystem.mock_identity (individual_id,identity_json) VALUES')[0]
    (RUNTIME / 'init.sql').write_text(upstream, encoding='utf-8')
    flow=(INFRA/'upstream/flow-esignet.yaml').read_text(encoding='utf-8')
    captcha='  - ref: CAPTCHA_BOX\n    identifier: captcha_token\n    type: CUSTOM\n    required: true\n'
    if captcha not in flow:
        raise SystemExit('The pinned demo flow has an unexpected format.')
    flow=flow.replace(captcha,'')
    flow='\n'.join(line for line in flow.split('\n') if line.strip()!='- *captcha_box' and not line.strip().startswith('- *login_id_'))
    flow=flow.replace('label: "{{t(input:uin_label)}}"','label: "Test identity number"')
    flow=flow.replace('placeholder: "{{t(input:uin_placeholder)}}"','placeholder: "Enter the test identity number"')
    flow=flow.replace('value: ^[0-9]+$', 'value: ^[0-9]{12}$')
    flow=flow.replace('value: 16\n      message: "validation.uin.length"','value: 12\n      message: "validation.uin.length"')
    flow=flow.replace('value: 10\n      message: "validation.uin.length"','value: 12\n      message: "validation.uin.length"')
    # Preserve OTP_INPUT: the provider uses its type to classify sensitive
    # credentials. A plain text field would not reach the OTP authenticator.
    flow=flow.replace('label: "{{t(otp:otp_sent_msg)}}"','label: "Demo OTP (111111)"')
    (RUNTIME/'flow.yaml').write_text(flow,encoding='utf-8')
    (RUNTIME/'ui-config.js').write_text('window.assessbridgeIdentityProvider = '+json.dumps({'returnUrl':app_url+'/exam-room'})+';\n',encoding='utf-8')
    client = 'assessbridge-demo'
    callback = app_url + '/identity/esignet/callback'
    name = json.dumps({'eng': 'AssessBridge demo'})
    fields = {'id': client, 'name': name, 'rp_id': 'assessbridge-demo',
              'logo_uri': app_url + '/favicon.ico', 'redirect_uris': json.dumps([callback]),
              'claims': json.dumps(['name']), 'acr_values': json.dumps(['mosip:idp:acr:generated-code']),
              'public_key': json.dumps(jwk, sort_keys=True), 'public_key_hash': hashlib.sha256(json.dumps(jwk, sort_keys=True).encode()).hexdigest(),
              'grant_types': json.dumps(['authorization_code']), 'auth_methods': json.dumps(['private_key_jwt']),
              'status': 'ACTIVE', 'additional_config': json.dumps({'require_pkce': True, 'require_pushed_authorization_requests': False, 'dpop_bound_access_tokens': False})}
    sql = '\\connect mosip_esignet\n'
    sql += 'INSERT INTO esignet.client_detail (' + ','.join(fields) + ',cr_dtimes) VALUES (' + ','.join(quote(v) for v in fields.values()) + ',CURRENT_TIMESTAMP) ON CONFLICT(id) DO UPDATE SET ' + ','.join(k+'=EXCLUDED.'+k for k in fields if k!='id') + ';\n'
    sql += '\\connect mosip_mockidentitysystem\n'
    identities = json.loads((INFRA / 'identities.json').read_text())
    # Deliberately fictional records. No personal photos or real contact data.
    for identity in identities:
        record = {**identity, 'pin': '545411', 'preferredLang': 'eng', 'dateOfBirth': '1995/01/01',
                  'email': identity['individualId']+'@example.invalid', 'phone': '+00000000000', 'postalCode': '00000',
                  'encodedPhoto': '', 'gender': 'Not specified', 'locality': 'Demo City', 'region': 'Demo Region', 'country': 'Demo'}
        for key in ('fullName','givenName','familyName','gender','locality','region','country'):
            record[key] = [{'language': 'eng', 'value': record[key]}]
        sql += 'INSERT INTO mockidentitysystem.mock_identity(individual_id,identity_json) VALUES(' + quote(identity['individualId']) + ',' + quote(json.dumps(record)) + ') ON CONFLICT(individual_id) DO UPDATE SET identity_json=EXCLUDED.identity_json;\n'
    (RUNTIME / 'bootstrap.sql').write_text(sql, encoding='utf-8')
    app_env = ROOT / '.env'
    app_env.touch(exist_ok=True)
    for key, value in {'ESIGNET_DEMO_ENABLED':'true', 'ESIGNET_ISSUER':issuer, 'ESIGNET_CLIENT_ID':client,
                       'ESIGNET_REDIRECT_URI':callback, 'ESIGNET_PRIVATE_KEY_FILE':'var/esignet/client-private.pem'}.items():
        set_key(str(app_env), key, value)
    return identities


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--issuer', default='http://127.0.0.1:3000')
    parser.add_argument('--app-url', default='http://127.0.0.1:8000')
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--stop', action='store_true', help='Stop the demo containers without deleting their data')
    args = parser.parse_args()
    if args.stop:
        docker,path=docker_command()
        subprocess.run(docker+['compose','--env-file',path(RUNTIME/'runtime.env'),'-f',path(INFRA/'compose.yaml'),'stop'],check=True)
        return
    identities = prepare(args.issuer.rstrip('/'), args.app_url.rstrip('/'))
    if args.prepare_only:
        print('Demo configuration prepared. Start the services with this command again without --prepare-only.')
        return
    docker, path = docker_command()
    keep_wsl_running(docker)
    compose = docker + ['compose', '--progress', 'plain', '--env-file', path(RUNTIME/'runtime.env'), '-f', path(INFRA/'compose.yaml')]
    subprocess.run(compose + ['up','-d','database'], check=True)
    for _ in range(60):
        result = subprocess.run(compose + ['exec','-T','database','pg_isready','-U','postgres'], capture_output=True)
        if result.returncode == 0:
            break
        time.sleep(1)
    else:
        raise SystemExit('The demo database did not become ready. Check docker compose logs.')
    subprocess.run(compose + ['exec','-T','database','psql','-U','postgres','-v','ON_ERROR_STOP=1'], input=(RUNTIME/'bootstrap.sql').read_bytes(), check=True)
    # Reload client configuration after an intentional, idempotent bootstrap.
    subprocess.run(compose + ['up','-d','mock-identity-system','esignet','esignet-ui'], check=True)
    # Recreate to refresh file bind mounts after source updates (including
    # atomic file replacement by Windows editors).
    subprocess.run(compose + ['up','-d','--force-recreate','esignet-ui'], check=True)
    subprocess.run(compose + ['restart','esignet'], check=True)
    for _ in range(90):
        try:
            with urlopen(args.issuer.rstrip('/')+'/.well-known/openid-configuration',timeout=3) as response:
                if json.load(response).get('issuer')==args.issuer.rstrip('/'):
                    break
        except (OSError,ValueError):
            pass
        time.sleep(1)
    else:
        raise SystemExit('The identity provider is not ready. Check the demo service logs before using it.')
    print('Demo services are ready. Restart FastAPI to load its demo configuration.')
    print('Test OTP: 111111 (fictional identities only)')
    for identity in identities:
        print(identity['individualId'] + ' - ' + identity['fullName'])


if __name__ == '__main__':
    main()
