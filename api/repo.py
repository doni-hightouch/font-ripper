from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse
import urllib.request, json, os, base64

GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN', '')
GITHUB_REPO  = os.environ.get('GITHUB_REPO', '')
FILE_PATH    = 'repository.json'
API_BASE     = 'https://api.github.com'
MAX_ENTRIES  = 500


def gh_headers():
    return {
        'Authorization': f'token {GITHUB_TOKEN}',
        'Accept': 'application/vnd.github.v3+json',
        'User-Agent': 'font-ripper',
        'Content-Type': 'application/json',
    }


def get_file():
    url = f'{API_BASE}/repos/{GITHUB_REPO}/contents/{FILE_PATH}'
    req = urllib.request.Request(url, headers=gh_headers())
    try:
        r = urllib.request.urlopen(req, timeout=10)
        data = json.loads(r.read())
        content = json.loads(base64.b64decode(data['content']).decode())
        return content, data['sha']
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return [], None
        raise


def put_file(entries, sha, message='update repository'):
    url  = f'{API_BASE}/repos/{GITHUB_REPO}/contents/{FILE_PATH}'
    body = {
        'message': message,
        'content': base64.b64encode(json.dumps(entries, indent=2).encode()).decode(),
    }
    if sha:
        body['sha'] = sha
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers=gh_headers(), method='PUT')
    urllib.request.urlopen(req, timeout=10)


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            entries, _ = get_file()
            self._json(200, entries)
        except Exception as e:
            self._json(500, {'error': str(e)})

    def do_POST(self):
        try:
            length = int(self.headers.get('Content-Length', 0))
            body   = json.loads(self.rfile.read(length))
            domain = body.get('domain', '')
            fonts  = body.get('fonts', [])
            ts     = body.get('ts', '')
            if not domain or not fonts:
                self._json(400, {'error': 'domain and fonts required'})
                return

            entry = {'domain': domain, 'fonts': fonts, 'ts': ts, 'count': len(fonts)}

            entries, sha = get_file()
            entries.insert(0, entry)
            entries = entries[:MAX_ENTRIES]
            put_file(entries, sha, f'rip: {domain} ({len(fonts)} fonts)')
            self._json(200, {'ok': True})
        except Exception as e:
            self._json(500, {'error': str(e)})

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def _json(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def log_message(self, *args):
        pass
