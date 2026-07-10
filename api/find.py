from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, quote
import urllib.request, re, json

UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
      'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')


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


# Font Squirrel's catalog is fetched once per warm instance (their API is slow/flaky).
_FS_LIST = None

def _fs_catalog():
    global _FS_LIST
    if _FS_LIST is not None:
        return _FS_LIST
    try:
        r = urllib.request.urlopen(urllib.request.Request(
            'https://www.fontsquirrel.com/api/fontlist/all',
            headers={'User-Agent': UA}), timeout=12)
        _FS_LIST = json.loads(r.read().decode('utf-8', 'ignore'))
        return _FS_LIST
    except Exception:
        return []   # don't cache the failure — allow a later retry


def check_font_squirrel(name):
    """Look a typeface up in Font Squirrel (free-for-commercial fonts, incl. many
    display faces not on Google Fonts). Downloads are ZIP archives."""
    low = name.lower().strip()
    for f in _fs_catalog():
        if f.get('family_name', '').lower() == low:
            slug = f.get('family_urlname')
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
