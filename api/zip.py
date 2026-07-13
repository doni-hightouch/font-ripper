from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse
import urllib.request, io, re, json, base64, zipfile

UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
      'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')

WOFF_PAT = re.compile(r'\.woff2?($|\?)', re.I)
EXT_PAT  = re.compile(r'\.(ttf|otf|eot)($|\?)', re.I)


def fetch_bytes(url, site):
    h = {'User-Agent': UA, 'Accept': '*/*'}
    if site:
        h['Referer'] = site
        p = urlparse(site)
        h['Origin'] = f'{p.scheme}://{p.netloc}'
    r = urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=15)
    return r.read()


def to_ttf(data):
    from fontTools.ttLib import TTFont
    bi, bo = io.BytesIO(data), io.BytesIO()
    f = TTFont(bi)
    f.flavor = None
    f.save(bo)
    return bo.getvalue()


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            length = int(self.headers.get('Content-Length', 0))
            body   = json.loads(self.rfile.read(length))
            site   = body.get('site', '')
            fonts  = body.get('fonts', [])

            buf = io.BytesIO()
            used = set()
            added = 0
            with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
                for f in fonts:
                    url = f.get('url')
                    nm  = f.get('name') or 'font'
                    if not url:
                        continue
                    try:
                        if url.startswith('data:'):
                            header, b64 = url.split(',', 1)
                            data = base64.b64decode(b64)
                            is_woff = 'woff' in header.lower()
                        else:
                            data = fetch_bytes(url, site)
                            is_woff = bool(WOFF_PAT.search(url))
                        if not data or len(data) < 200:
                            continue

                        if is_woff:
                            try:
                                data = to_ttf(data)
                                ext = '.ttf'
                            except Exception:
                                ext = '.woff2' if 'woff2' in url.lower() else '.woff'
                        else:
                            m = EXT_PAT.search(url)
                            ext = '.' + (m.group(1).lower() if m else 'ttf')

                        fname = re.sub(r'[^A-Za-z0-9_.-]', '_', nm) + ext
                        while fname in used:
                            fname = '_' + fname
                        used.add(fname)
                        z.writestr(fname, data)
                        added += 1
                    except Exception:
                        continue

            if added == 0:
                self._json(502, {'error': 'no fonts could be downloaded'})
                return

            data = buf.getvalue()
            self.send_response(200)
            self.send_header('Content-Type', 'application/zip')
            self.send_header('Content-Disposition', 'attachment; filename="fonts.zip"')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            self._json(500, {'error': str(e)})

    def _json(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass
