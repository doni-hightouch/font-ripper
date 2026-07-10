from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, urljoin, quote
import urllib.request, re, json, os
from pathlib import Path

SCRAPER_KEY = os.environ.get('SCRAPER_API_KEY', '')


def scraper_url(target):
    """Route a fetch through ScraperAPI (residential proxy, beats Cloudflare)."""
    return f'https://api.scraperapi.com/?api_key={SCRAPER_KEY}&url={quote(target, safe="")}'

FONT_PAT = re.compile(r'\.(woff2?|ttf|otf|eot)(\?[^"\')\s]*)?', re.I)
DATA_FONT = re.compile(r'url\(["\']?(data:font/([^;]+);base64,([A-Za-z0-9+/=\s]+))["\']?\)', re.I)
UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
      'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')


def fetch(url, referer=None):
    # Layer 1: direct urllib — free, works for the majority of sites.
    headers = {'User-Agent': UA, 'Accept': '*/*'}
    if referer:
        headers['Referer'] = referer
        p = urlparse(referer)
        headers['Origin'] = f'{p.scheme}://{p.netloc}'
    try:
        r = urllib.request.urlopen(
            urllib.request.Request(url, headers=headers), timeout=12)
        text = r.read().decode('utf-8', errors='ignore')
        if text:
            return text
    except Exception:
        pass

    # Layer 2: ScraperAPI fallback — residential proxy that gets past
    # Cloudflare / bot protection that blocks datacenter requests.
    if SCRAPER_KEY:
        try:
            r = urllib.request.urlopen(scraper_url(url), timeout=60)
            return r.read().decode('utf-8', errors='ignore')
        except Exception:
            pass

    return ''


def font_urls_in_css(css, base):
    found = set()
    for m in re.finditer(r'url\(["\']?([^"\')\s]+)["\']?\)', css, re.I):
        u = m.group(1).split('?')[0]
        if FONT_PAT.search(u):
            found.add(urljoin(base, u))
    return found


def data_fonts_in_css(css):
    """Extract base64-embedded fonts from @font-face blocks, pairing with font-family name."""
    results = []
    seen = set()
    # Find each @font-face block
    for block in re.finditer(r'@font-face\s*\{([^}]+)\}', css, re.S | re.I):
        block_text = block.group(1)
        # Get font-family name
        family_m = re.search(r'font-family\s*:\s*["\']?([^"\';\n]+)["\']?', block_text, re.I)
        family = family_m.group(1).strip().strip('"\'') if family_m else 'font'
        # Get all data URI fonts in this block
        for m in DATA_FONT.finditer(block_text):
            fmt = m.group(2).replace('woff2', 'woff2').replace('woff', 'woff')
            b64 = m.group(3).replace('\n', '').replace(' ', '')
            key = b64[:40]
            if key in seen:
                continue
            seen.add(key)
            results.append({
                'family': family,
                'fmt': fmt,
                'b64': b64,
            })
    return results


def stylesheet_links(text, base, is_css=False):
    urls = []
    if not is_css:
        for m in re.finditer(
                r'<link[^>]+rel=["\']stylesheet["\'][^>]*href=["\']([^"\']+)["\']'
                r'|<link[^>]+href=["\']([^"\']+)["\'][^>]*rel=["\']stylesheet["\']',
                text, re.I):
            href = m.group(1) or m.group(2)
            if href:
                urls.append(urljoin(base, href))
    for m in re.finditer(r'@import\s+(?:url\(["\']?|["\'])([^"\')\s]+)', text, re.I):
        urls.append(urljoin(base, m.group(1)))
    return list(dict.fromkeys(urls))


def scrape(site_url):
    if not site_url.startswith('http'):
        site_url = 'https://' + site_url

    html = fetch(site_url)
    if not html:
        return site_url, []

    found_urls = set()
    data_fonts = []

    inline = '\n'.join(re.findall(r'<style[^>]*>(.*?)</style>', html, re.S | re.I))
    found_urls |= font_urls_in_css(inline, site_url)
    data_fonts += data_fonts_in_css(inline)

    visited, queue = set(), stylesheet_links(html, site_url)
    while queue:
        css_url = queue.pop(0)
        if css_url in visited:
            continue
        visited.add(css_url)
        css = fetch(css_url, referer=site_url)
        if not css:
            continue
        found_urls |= font_urls_in_css(css, css_url)
        data_fonts += data_fonts_in_css(css)
        for imp in stylesheet_links(css, css_url, is_css=True):
            if imp not in visited:
                queue.append(imp)

    # Deduplicate external URL fonts, prefer woff2
    PREF = {'woff2': 0, 'woff': 1, 'ttf': 2, 'otf': 2, 'eot': 3}
    by_base = {}
    for url in found_urls:
        name = Path(urlparse(url).path).name
        ext = FONT_PAT.search(name)
        if not ext:
            continue
        ext_str = ext.group(1).lower()
        base = re.sub(r'\.' + ext_str + r'$', '', name, flags=re.I)
        rank = PREF.get(ext_str, 99)
        if base not in by_base or rank < by_base[base][0]:
            by_base[base] = (rank, url, name, ext_str)

    fonts = []
    for base, (rank, url, name, ext_str) in sorted(by_base.items()):
        stem = re.sub(r'\.' + ext_str + r'$', '', name, flags=re.I)
        fonts.append({'name': stem, 'fmt': ext_str.upper(), 'url': url})

    # Add base64-embedded fonts (dedupe by family+fmt)
    seen_families = set()
    for df in data_fonts:
        key = (df['family'].lower(), df['fmt'])
        if key in seen_families:
            continue
        seen_families.add(key)
        name = re.sub(r'[^A-Za-z0-9_-]', '-', df['family'])
        fonts.append({
            'name': name,
            'fmt': df['fmt'].upper(),
            'url': f"data:font/{df['fmt']};base64,{df['b64']}",
        })

    domain = urlparse(site_url).hostname or site_url
    domain = domain.replace('www.', '')
    return domain, fonts


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        query = parse_qs(urlparse(self.path).query)
        url = query.get('url', [''])[0]

        if not url:
            self._json(400, {'error': 'url parameter required'})
            return

        # Temporary diagnostic: /api/rip?url=...&debug=1
        if query.get('debug', [''])[0]:
            self._json(200, self._diagnose(url))
            return

        try:
            domain, fonts = scrape(url)
            self._json(200, {'domain': domain, 'fonts': fonts})
        except Exception as e:
            self._json(500, {'error': str(e)})

    def _diagnose(self, url):
        if not url.startswith('http'):
            url = 'https://' + url
        out = {'key_present': bool(SCRAPER_KEY), 'key_len': len(SCRAPER_KEY)}
        # Direct urllib attempt
        try:
            r = urllib.request.urlopen(
                urllib.request.Request(url, headers={'User-Agent': UA}), timeout=12)
            out['direct'] = {'status': r.status, 'len': len(r.read())}
        except Exception as e:
            out['direct'] = {'error': type(e).__name__ + ': ' + str(e)[:120]}
        # ScraperAPI attempt
        if SCRAPER_KEY:
            try:
                r = urllib.request.urlopen(scraper_url(url), timeout=60)
                body = r.read()
                out['scraper'] = {'status': r.status, 'len': len(body)}
            except Exception as e:
                out['scraper'] = {'error': type(e).__name__ + ': ' + str(e)[:200]}
        return out

    def _json(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass
