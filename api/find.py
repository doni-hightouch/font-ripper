from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, quote
import urllib.request, re, json, os

UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
      'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')

# Bundled Font Squirrel catalog: {lowercase family name -> download slug}.
# Snapshotted because their live API is slow/flaky and blocks datacenter IPs.
try:
    with open(os.path.join(os.path.dirname(__file__), 'fs_catalog.json')) as _f:
        FS_CATALOG = json.load(_f)
except Exception:
    FS_CATALOG = {}


def check_google_fonts(name):
    """Look a typeface up in Google Fonts (free, open-source, keyless API)."""
    api = 'https://fonts.googleapis.com/css2?family=' + quote(name) + ':wght@400;700'
    try:
        r = urllib.request.urlopen(
            urllib.request.Request(api, headers={'User-Agent': UA}), timeout=10)
        css = r.read().decode('utf-8', 'ignore')
        woffs = re.findall(r'url\((https://[^)]+\.woff2?)\)', css)
        if woffs:
            page = 'https://fonts.google.com/specimen/' + quote(name.replace(' ', '+'), safe='+')
            return {'found': True, 'source': 'Google Fonts', 'url': woffs[0],
                    'zip': False, 'page': page}
    except Exception:
        pass
    return {'found': False}


def check_font_squirrel(name):
    """Look a typeface up in Font Squirrel (free-for-commercial fonts, incl. many
    display faces not on Google Fonts). Downloads are ZIP archives."""
    slug = FS_CATALOG.get(name.lower().strip())
    if slug:
        return {'found': True, 'source': 'Font Squirrel', 'zip': True,
                'url': 'https://www.fontsquirrel.com/fonts/download/' + slug,
                'page': 'https://www.fontsquirrel.com/fonts/' + slug}
    return {'found': False}


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        query = parse_qs(urlparse(self.path).query)
        name = query.get('name', [''])[0].strip()
        if not name:
            self._json(400, {'error': 'name parameter required'})
            return

        result = check_google_fonts(name)
        if not result.get('found'):
            result = check_font_squirrel(name)
        self._json(200, result)

    def _json(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass
