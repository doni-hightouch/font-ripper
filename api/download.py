from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, quote
import urllib.request, io, re, os

UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
      'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')

WOFF_PAT = re.compile(r'\.woff2?(\?.*)?$', re.I)

SCRAPER_KEY = os.environ.get('SCRAPER_API_KEY', '')


def fetch_bytes(url, headers):
    """Fetch binary content: direct first, then ScraperAPI residential proxy."""
    # Layer 1: direct urllib
    try:
        h = {'User-Agent': UA, 'Accept': '*/*'}
        h.update(headers)
        r = urllib.request.urlopen(
            urllib.request.Request(url, headers=h), timeout=15)
        data = r.read()
        if data and len(data) > 200:
            return data
    except Exception:
        pass

    # Layer 2: ScraperAPI fallback for protected origins
    if SCRAPER_KEY:
        api = f'https://api.scraperapi.com/?api_key={SCRAPER_KEY}&url={quote(url, safe="")}'
        r = urllib.request.urlopen(api, timeout=60)
        return r.read()

    raise RuntimeError('font fetch failed')


def to_ttf(data, src_url):
    """Convert woff/woff2 bytes to TTF bytes. Returns (ttf_bytes, filename)."""
    from fontTools.ttLib import TTFont
    src_name = src_url.split('/')[-1].split('?')[0]
    stem = re.sub(r'\.woff2?$', '', src_name, flags=re.I)
    buf_in  = io.BytesIO(data)
    buf_out = io.BytesIO()
    f = TTFont(buf_in)
    f.flavor = None
    f.save(buf_out)
    return buf_out.getvalue(), stem + '.ttf'


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        query = parse_qs(urlparse(self.path).query)
        url  = query.get('url',  [''])[0]
        site = query.get('site', [''])[0]

        if not url:
            self.send_response(400)
            self.end_headers()
            return

        headers = {}
        if site:
            headers['Referer'] = site
            p = urlparse(site)
            headers['Origin'] = f'{p.scheme}://{p.netloc}'

        try:
            data = fetch_bytes(url, headers)

            if WOFF_PAT.search(url) and len(data) > 500:
                try:
                    data, filename = to_ttf(data, url)
                except Exception:
                    filename = url.split('/')[-1].split('?')[0] or 'font'
            else:
                filename = url.split('/')[-1].split('?')[0] or 'font'

            self.send_response(200)
            self.send_header('Content-Type', 'application/octet-stream')
            self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            self.send_response(502)
            self.end_headers()
            self.wfile.write(str(e).encode())

    def log_message(self, *args):
        pass
