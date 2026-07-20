from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, urljoin, quote
import urllib.request, re, json, os
from pathlib import Path

SCRAPER_KEY = os.environ.get('SCRAPER_API_KEY', '')

# Generic / system keywords that aren't real typeface names.
GENERIC = {'serif', 'sans-serif', 'monospace', 'cursive', 'fantasy', 'system-ui',
    'ui-sans-serif', 'ui-serif', 'ui-monospace', 'ui-rounded', 'inherit', 'initial',
    'unset', 'revert', 'none', 'auto', 'math', 'emoji', 'fangsong',
    '-apple-system', 'blinkmacsystemfont'}


def scraper_url(target):
    """Route a fetch through ScraperAPI (residential proxy, beats Cloudflare)."""
    return f'https://api.scraperapi.com/?api_key={SCRAPER_KEY}&url={quote(target, safe="")}'


def collect_families(text, count, disp):
    """Tally font-family names declared in CSS (what the site actually renders in)."""
    for m in re.finditer(r'font-family\s*:\s*([^;{}]+)', text, re.I):
        val = re.sub(r'!important', '', m.group(1), flags=re.I)
        for part in val.split(','):
            n = part.strip().strip('\'"').strip()
            low = n.lower()
            if not n or low in GENERIC:
                continue
            if low.startswith('var(') or '\\' in n or len(n) > 40:
                continue
            if not re.search(r'[A-Za-z]', n):
                continue
            count[low] = count.get(low, 0) + 1
            disp.setdefault(low, n)

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


def font_faces_in_css(css, base, name_map, fam_map):
    """Map each font file URL -> a human name from its @font-face rule, so build
    systems that hash filenames (Next.js etc.) still show 'Mulish-700', not a hash.
    Also record the raw family name per URL (for usage/role lookup)."""
    for block in re.finditer(r'@font-face\s*\{([^}]*)\}', css, re.S | re.I):
        b = block.group(1)
        fam_m = re.search(r'font-family\s*:\s*([^;]+)', b, re.I)
        if not fam_m:
            continue
        raw_family = fam_m.group(1).strip().strip('\'"').strip()
        family = re.sub(r'\s+', '', raw_family)
        if not family:
            continue
        name = family
        wt = re.search(r'font-weight\s*:\s*([^;]+)', b, re.I)
        if wt:
            nums = re.findall(r'\d{3}', wt.group(1))
            kw = wt.group(1).strip().lower()
            if len(nums) >= 2:          # a range → variable font
                name += '-VF'
            elif len(nums) == 1:
                name += '-' + nums[0]
            elif kw == 'bold':
                name += '-700'
        st = re.search(r'font-style\s*:\s*([^;]+)', b, re.I)
        if st and 'italic' in st.group(1).lower():
            name += '-italic'
        for m in re.finditer(r'url\(["\']?([^"\')\s]+)["\']?\)', b, re.I):
            u = m.group(1).split('?')[0]
            if FONT_PAT.search(u):
                full = urljoin(base, u)
                name_map[full] = name
                fam_map[full] = raw_family.lower()


# Which selectors / design-token names imply which usage role (priority order).
ROLE_PATTERNS = [
    ('Headings', re.compile(r'(?:^|[^a-z])h[1-6](?![a-z])|head(?:ing|line)|(?<![a-z])title|display|hero|font-heading|heading-font|font-display', re.I)),
    ('Body',     re.compile(r'(?<![a-z])body(?![a-z])|(?<![a-z])html(?![a-z])|paragraph|prose|(?<![a-z])p(?![a-z])|copy|font-body|body-font|font-base|font-sans|font-text|font-serif|font-primary', re.I)),
    ('Code',     re.compile(r'(?<![a-z])(?:code|pre|kbd|samp)(?![a-z])|(?<![a-z])mono|font-mono', re.I)),
    ('UI',       re.compile(r'button|(?<![a-z])btn|(?<![a-z])nav|label|caption|badge|(?<![a-z])menu|font-ui', re.I)),
]


def _classify(selector):
    for role, pat in ROLE_PATTERNS:
        if pat.search(selector):
            return role
    return None


def collect_roles(css, votes):
    """Scan CSS rules + design tokens to guess how each font-family is used."""
    # Design tokens: --font-heading: 'Mulish', ...  → role from the token name.
    for m in re.finditer(r'(--[\w-]*font[\w-]*)\s*:\s*([^;{}]+)', css, re.I):
        role = _classify(m.group(1))
        if not role:
            continue
        for fam in _families_in_value(m.group(2)):
            votes.setdefault(fam, {}).setdefault(role, 0)
            votes[fam][role] += 2       # token names are strong signals

    # Rules: selector { ... font-family: ... }
    for m in re.finditer(r'([^{}]+)\{([^{}]*)\}', css):
        selector, body = m.group(1), m.group(2)
        if 'font' not in body.lower():
            continue
        role = _classify(selector)
        if not role:
            continue
        ff = re.search(r'font-family\s*:\s*([^;]+)', body, re.I)
        if not ff:
            continue
        for fam in _families_in_value(ff.group(1)):
            votes.setdefault(fam, {}).setdefault(role, 0)
            votes[fam][role] += 1


def _families_in_value(value):
    """Yield real (non-generic, non-var) family names, lowercased, from a CSS value."""
    out = []
    for part in re.sub(r'!important', '', value, flags=re.I).split(','):
        n = part.strip().strip('\'"').strip().lower()
        if not n or n in GENERIC or n.startswith('var(') or '\\' in n or len(n) > 40:
            continue
        if not re.search(r'[a-z]', n):
            continue
        out.append(n)
    return out


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

    domain = (urlparse(site_url).hostname or site_url).replace('www.', '')

    html = fetch(site_url)
    if not html:
        return domain, [], False, []

    found_urls = set()
    data_fonts = []
    fam_count, fam_disp = {}, {}
    name_map, fam_map, role_votes = {}, {}, {}
    collect_families(html, fam_count, fam_disp)
    font_faces_in_css(html, site_url, name_map, fam_map)
    collect_roles(html, role_votes)

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
        collect_families(css, fam_count, fam_disp)
        font_faces_in_css(css, css_url, name_map, fam_map)
        collect_roles(css, role_votes)
        for imp in stylesheet_links(css, css_url, is_css=True):
            if imp not in visited:
                queue.append(imp)

    # Resolve one role per family (highest vote wins).
    fam_role = {}
    for fam, votes in role_votes.items():
        fam_role[fam] = max(votes, key=votes.get)

    def role_for(url, family_hint=''):
        fam = (fam_map.get(url) or family_hint or '').lower()
        return fam_role.get(fam, '')

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
    used = set()
    for base, (rank, url, name, ext_str) in sorted(by_base.items()):
        stem = re.sub(r'\.' + ext_str + r'$', '', name, flags=re.I)
        # Prefer the real name from the @font-face rule over a hashed filename.
        nice = name_map.get(url, stem)
        if nice != stem and nice in used:      # same family+weight (e.g. subsets)
            nice = nice + '-' + stem[:6]
        used.add(nice)
        fonts.append({'name': nice, 'fmt': ext_str.upper(), 'url': url,
                      'role': role_for(url)})

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
            'role': role_for('', df['family']),
        })

    # Order: primary typefaces first (headings/body), utility & unknown last.
    ROLE_ORDER = {'Headings': 0, 'Body': 1, 'Display': 2, 'UI': 3, 'Code': 4, '': 5}
    fonts.sort(key=lambda f: (ROLE_ORDER.get(f.get('role', ''), 5), f['name'].lower()))

    ranked = sorted(fam_count, key=lambda k: -fam_count[k])
    families = [fam_disp[k] for k in ranked][:15]

    return domain, fonts, True, families


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        query = parse_qs(urlparse(self.path).query)
        url = query.get('url', [''])[0]

        if not url:
            self._json(400, {'error': 'url parameter required'})
            return

        try:
            domain, fonts, loaded, families = scrape(url)
            resp = {'domain': domain, 'fonts': fonts, 'families': families}
            if not fonts:
                if not loaded:
                    resp['reason'] = ("We couldn't open this site — it's blocking "
                                      "automated visits, so there was nothing for us to read.")
                else:
                    resp['reason'] = ("We opened the site but didn't find any downloadable "
                                      "fonts. It's probably using standard system fonts, or it "
                                      "loads its fonts in a way we can't reach from the outside.")
            self._json(200, resp)
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
