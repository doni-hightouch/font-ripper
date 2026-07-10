from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import urllib.request

UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
      'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        query = parse_qs(urlparse(self.path).query)
        url  = query.get('url',  [''])[0]
        site = query.get('site', [''])[0]

        if not url:
            self.send_response(400)
            self.end_headers()
            return

        headers = {'User-Agent': UA, 'Accept': '*/*'}
        if site:
            headers['Referer'] = site
            p = urlparse(site)
            headers['Origin'] = f'{p.scheme}://{p.netloc}'

        try:
            r = urllib.request.urlopen(
                urllib.request.Request(url, headers=headers), timeout=15)
            data = r.read()
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
