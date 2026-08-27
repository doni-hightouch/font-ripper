from http.server import BaseHTTPRequestHandler
import urllib.request, urllib.parse, json, os

# Vercel KV / Upstash Redis — credentials injected by the Vercel storage
# integration and managed by Vercel, so nothing expires (unlike a GitHub PAT).
KV_URL = (os.environ.get('KV_REST_API_URL')
          or os.environ.get('UPSTASH_REDIS_REST_URL', '')).rstrip('/')
KV_TOKEN = (os.environ.get('KV_REST_API_TOKEN')
            or os.environ.get('UPSTASH_REDIS_REST_TOKEN', ''))

KEY = 'repository'
MAX_ENTRIES = 1000
# Public raw file — used once to import the existing history into KV.
SEED_URL = 'https://raw.githubusercontent.com/doni-hightouch/font-ripper/main/repository.json'


def _kv(path, body=None):
    """Call the Redis REST API. GET when body is None, else POST with raw body."""
    url = f'{KV_URL}/{path}'
    req = urllib.request.Request(url, method='POST' if body is not None else 'GET')
    req.add_header('Authorization', f'Bearer {KV_TOKEN}')
    if body is not None:
        req.data = body if isinstance(body, bytes) else body.encode()
    r = urllib.request.urlopen(req, timeout=10)
    return json.loads(r.read().decode())


def get_entries():
    if not KV_URL or not KV_TOKEN:
        return []
    res = _kv(f'lrange/{KEY}/0/{MAX_ENTRIES - 1}')
    raw = res.get('result') or []
    entries = []
    for item in raw:
        try:
            entries.append(json.loads(item))
        except Exception:
            pass
    if entries:
        return entries
    return _seed()


def _seed():
    """First run: import the existing repository.json history from the public raw URL."""
    try:
        data = urllib.request.urlopen(SEED_URL, timeout=10).read().decode()
        entries = json.loads(data)
        if isinstance(entries, list) and entries:
            entries = entries[:MAX_ENTRIES]
            # RPUSH in order so index 0 stays newest.
            for e in entries:
                _kv(f'rpush/{KEY}', json.dumps(e))
            return entries
    except Exception:
        pass
    return []


def add_entry(entry):
    _kv(f'lpush/{KEY}', json.dumps(entry))          # newest at head
    _kv(f'ltrim/{KEY}/0/{MAX_ENTRIES - 1}')         # bound the list


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            self._json(200, get_entries())
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
            if not KV_URL or not KV_TOKEN:
                self._json(503, {'error': 'storage not configured'})
                return
            # Make sure history is imported before the first append.
            get_entries()
            add_entry({'domain': domain, 'fonts': fonts, 'ts': ts, 'count': len(fonts)})
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
