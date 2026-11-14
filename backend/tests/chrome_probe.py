"""Small standard-library Chrome DevTools driver for real-time browser checks."""
import base64
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import time
import urllib.request
from urllib.parse import urlsplit


class ChromeProbe:
    def __init__(self, profile, flags=()):
        self.profile = Path(profile)
        self.process = subprocess.Popen([r'C:\Program Files\Google\Chrome\Application\chrome.exe',
            '--headless=new', '--disable-gpu', '--no-first-run', '--disable-extensions', '--disable-background-mode', '--remote-debugging-port=0',
            '--user-data-dir='+str(self.profile), *flags, 'about:blank'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(200):
            port_file = self.profile/'DevToolsActivePort'
            try:
                port = int(port_file.read_text().splitlines()[0])
                break
            except (OSError,ValueError,IndexError):
                pass
            time.sleep(.1)
        else:
            self.process.terminate()
            raise RuntimeError('Chrome did not open its debug port')
        targets = json.load(urllib.request.urlopen(f'http://127.0.0.1:{port}/json'))
        url = urlsplit(next(t['webSocketDebuggerUrl'] for t in targets if t['type']=='page'))
        self.socket = socket.create_connection((url.hostname,url.port), timeout=100)
        key = base64.b64encode(os.urandom(16)).decode()
        self.socket.sendall(f'GET {url.path} HTTP/1.1\r\nHost: {url.hostname}:{url.port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n'.encode())
        response = b''
        while not response.endswith(b'\r\n\r\n'): response += self.socket.recv(1)
        if not response.startswith(b'HTTP/1.1 101 '):
            self.socket.close()
            self.process.terminate()
            self.process.wait(timeout=10)
            raise RuntimeError('Chrome debug connection failed: '+response.split(b'\r\n')[0].decode())
        self.sequence = 0
        self.events = []

    def exact(self, count):
        result = bytearray()
        while len(result) < count:
            chunk = self.socket.recv(count-len(result))
            if not chunk: raise RuntimeError('Chrome connection closed')
            result.extend(chunk)
        return bytes(result)

    def command(self, method, params=None):
        self.sequence += 1
        data = json.dumps({'id':self.sequence,'method':method,'params':params or {}}).encode()
        length = len(data)
        header = bytes([0x81,0x80|length]) if length<126 else bytes([0x81,0xfe])+struct.pack('!H',length) if length<65536 else bytes([0x81,0xff])+struct.pack('!Q',length)
        mask = os.urandom(4)
        self.socket.sendall(header+mask+bytes(v^mask[i%4] for i,v in enumerate(data)))
        assembled = bytearray()
        while True:
            first, second = self.exact(2)
            length = second & 127
            if length==126: length=struct.unpack('!H',self.exact(2))[0]
            if length==127: length=struct.unpack('!Q',self.exact(8))[0]
            mask = self.exact(4) if second&128 else None
            payload = self.exact(length)
            if mask: payload=bytes(v^mask[i%4] for i,v in enumerate(payload))
            if first&15==8: raise RuntimeError('Chrome closed the connection')
            if first&15 in (0,1): assembled.extend(payload)
            if first&128 and assembled:
                result=json.loads(assembled);assembled.clear()
                if result.get('method'):
                    self.events.append(result)
                    self.events=self.events[-500:]
                if result.get('id')==self.sequence:
                    if 'error' in result: raise RuntimeError(result['error'])
                    return result.get('result',{})

    def evaluate(self, expression):
        result=self.command('Runtime.evaluate',{'expression':expression,'awaitPromise':True,'returnByValue':True})
        if 'exceptionDetails' in result: raise RuntimeError(result['exceptionDetails'])
        return result.get('result',{}).get('value')

    def close(self):
        try: self.command('Browser.close')
        except (OSError,RuntimeError): pass
        self.socket.close()
        try: self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            if os.name == 'nt':
                # Chrome's child processes can retain the temporary profile.
                # Terminate only the process tree launched by this probe.
                subprocess.run(['taskkill','/PID',str(self.process.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            else:
                self.process.terminate()
            self.process.wait(timeout=10)
