#!/usr/bin/env python3
"""
The Font Ripper — extract fonts from any website.

Strategies (run in order, results merged):
  1. Static  — fetch HTML, follow all <link> stylesheets + @imports, parse @font-face
  2. Browser — headless Chromium renders the page, reads every loaded CSSFontFaceRule
               and intercepts live font network requests
  3. Download — deduplicate (prefer woff2), try multiple referers, save to brand folder

Usage:
  python fontdl.py <url> [output_dir] [--ttf]
"""

import re, sys, os
from pathlib import Path
from urllib.parse import urljoin, urlparse
import urllib.request, urllib.error

FONT_PAT   = re.compile(r'\.(woff2?|ttf|otf|eot)(\?[^"\')\s]*)?', re.I)
UA         = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
              'AppleWebKit/537.36 (KHTML, like Gecko) '
              'Chrome/124.0.0.0 Safari/537.36')

# ──────────────────────────────────────────────
# HTTP helpers
# ──────────────────────────────────────────────

def _req(url, referer=None, binary=False):
    headers = {'User-Agent': UA, 'Accept': '*/*'}
    if referer:
        headers['Referer'] = referer
        headers['Origin']  = urlparse(referer).scheme + '://' + urlparse(referer).netloc
    try:
        r = urllib.request.urlopen(
            urllib.request.Request(url, headers=headers), timeout=15)
        return r.read() if binary else r.read().decode('utf-8', errors='ignore')
    except Exception:
        return None

def fetch(url, referer=None):       return _req(url, referer) or ''
def fetch_bytes(url, referer=None): return _req(url, referer, binary=True)


# ──────────────────────────────────────────────
# CSS parsing helpers
# ──────────────────────────────────────────────

def font_urls_in_css(css, base):
    found = set()
    for m in re.finditer(r'url\(["\']?([^"\')\s]+)["\']?\)', css, re.I):
        u = m.group(1).split('?')[0]
        if FONT_PAT.search(u):
            found.add(urljoin(base, u))
    return found

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


# ──────────────────────────────────────────────
# Strategy 1 — static scraper
# ──────────────────────────────────────────────

def static_scrape(site_url):
    print('  [static] Fetching HTML...')
    html = fetch(site_url)
    if not html:
        print('  [static] Could not fetch page.')
        return set()

    found = set()

    inline = '\n'.join(re.findall(r'<style[^>]*>(.*?)</style>', html, re.S | re.I))
    found |= font_urls_in_css(inline, site_url)

    visited, queue = set(), stylesheet_links(html, site_url)
    print(f'  [static] {len(queue)} stylesheet(s) found')

    while queue:
        css_url = queue.pop(0)
        if css_url in visited:
            continue
        visited.add(css_url)
        css = fetch(css_url, referer=site_url)
        if not css:
            continue
        found |= font_urls_in_css(css, css_url)
        for imp in stylesheet_links(css, css_url, is_css=True):
            if imp not in visited:
                queue.append(imp)

    print(f'  [static] {len(found)} font URL(s) found')
    return found


# ──────────────────────────────────────────────
# Strategy 2 — headless browser
# ──────────────────────────────────────────────

_EXTRACT_JS = """
() => {
    const seen = new Set(), out = [];
    for (const sheet of document.styleSheets) {
        try {
            for (const rule of sheet.cssRules || []) {
                if (!(rule instanceof CSSFontFaceRule)) continue;
                const src = rule.style.getPropertyValue('src');
                const matches = [...src.matchAll(
                    /url\\(["']?([^"')\\s]+\\.(?:woff2?|ttf|otf|eot)[^"')\\s]*)["']?\\)/gi
                )];
                for (const [, raw] of matches) {
                    let resolved;
                    try {
                        const base = sheet.href || location.href;
                        resolved = new URL(raw, base).href.split('?')[0];
                    } catch { resolved = raw.split('?')[0]; }
                    if (!seen.has(resolved)) { seen.add(resolved); out.push(resolved); }
                }
            }
        } catch {}
    }
    return out;
}
"""

def browser_scrape(site_url):
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        print('  [browser] Playwright not installed — skipping.')
        return set()

    found       = set()
    intercepted = set()

    print('  [browser] Launching headless Chrome...')
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx     = browser.new_context(
            user_agent=UA,
            extra_http_headers={'Accept-Language': 'en-US,en;q=0.9'},
        )
        page = ctx.new_page()

        def on_response(resp):
            url = resp.url
            if FONT_PAT.search(url):
                intercepted.add(url.split('?')[0])

        page.on('response', on_response)

        try:
            page.goto(site_url, wait_until='networkidle', timeout=25_000)
        except PWTimeout:
            try:
                page.goto(site_url, wait_until='domcontentloaded', timeout=20_000)
                page.wait_for_timeout(4_000)
            except Exception:
                pass

        try:
            js_fonts = page.evaluate(_EXTRACT_JS)
            found.update(js_fonts)
        except Exception as e:
            print(f'  [browser] JS eval failed: {e}')

        found.update(intercepted)
        print(f'  [browser] {len(found)} font URL(s) found '
              f'({len(intercepted)} intercepted live)')
        browser.close()

    return found


# ──────────────────────────────────────────────
# Download
# ──────────────────────────────────────────────

def best_urls(all_urls):
    """For each base font name, keep only the best format (prefer woff2)."""
    PREF = {'woff2': 0, 'woff': 1, 'ttf': 2, 'otf': 2, 'eot': 3}

    by_base = {}
    for url in all_urls:
        name = Path(urlparse(url).path).name
        ext  = FONT_PAT.search(name)
        if not ext:
            continue
        ext_str = ext.group(1).lower()
        base = re.sub(r'\.' + ext_str + r'$', '', name.lower(), flags=re.I)
        rank = PREF.get(ext_str, 99)
        if base not in by_base or rank < by_base[base][0]:
            by_base[base] = (rank, url)

    return {url for _, url in by_base.values()}


def download(url, out_dir, site_url):
    name = Path(urlparse(url).path).name or url.split('/')[-1].split('?')[0]
    if not name or not FONT_PAT.search(name):
        return None, None

    dest = out_dir / name
    if dest.exists():
        return name, 'exists'

    origin = urlparse(site_url).scheme + '://' + urlparse(site_url).netloc + '/'
    for ref in [site_url, origin, None]:
        data = fetch_bytes(url, referer=ref)
        if data and len(data) > 500:
            dest.write_bytes(data)
            return name, len(data)

    return name, None


def convert_ttf(out_dir):
    try:
        from fontTools.ttLib import TTFont
    except ImportError:
        print('\n  [ttf] fonttools not installed — run: pip3 install fonttools brotli')
        return
    count = 0
    sources = list(out_dir.glob('*.woff2')) + list(out_dir.glob('*.woff'))
    for src in sources:
        ttf_path = src.with_suffix('.ttf')
        if ttf_path.exists():
            continue
        try:
            f = TTFont(src)
            f.flavor = None
            f.save(ttf_path)
            print(f'  [ttf] {src.name} → {ttf_path.name}')
            count += 1
        except Exception as e:
            print(f'  [ttf] failed {src.name}: {e}')
    print(f'  [ttf] {count} file(s) converted')


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print('Usage: python fontdl.py <url> [output_dir] [--ttf]')
        sys.exit(1)

    site_url = sys.argv[1]
    if not site_url.startswith('http'):
        site_url = 'https://' + site_url

    args     = sys.argv[2:]
    do_ttf   = '--ttf' in args
    out_arg  = next((a for a in args if not a.startswith('-')), None)
    brand    = urlparse(site_url).hostname.replace('www.', '').split('.')[0]
    out_dir  = Path(out_arg or brand)
    out_dir.mkdir(exist_ok=True)

    print(f'\n✂  The Font Ripper — {site_url}')
    print('─' * 52)

    all_urls  = static_scrape(site_url)
    print()
    all_urls |= browser_scrape(site_url)

    if not all_urls:
        print('\nNo fonts found.')
        sys.exit(0)

    urls = best_urls(all_urls)
    print(f'\n  {len(all_urls)} total URL(s) → {len(urls)} unique font(s) after dedup\n')
    print(f"Downloading to '{out_dir}/'...\n")

    ok = skipped = failed = 0
    for url in sorted(urls):
        name, status = download(url, out_dir, site_url)
        if not name:
            continue
        if status == 'exists':
            print(f'  [exists] {name}')
            skipped += 1
        elif status:
            print(f'  [ok]     {name}  ({status // 1024} KB)')
            ok += 1
        else:
            print(f'  [skip]   {name}  (blocked or empty)')
            failed += 1

    print(f'\nDone — {ok} downloaded, {skipped} already existed, {failed} blocked.')

    if do_ttf:
        print()
        convert_ttf(out_dir)


if __name__ == '__main__':
    main()
