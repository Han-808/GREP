"""Version local homepage scripts/styles without rebuilding data pages."""
from pathlib import Path
import hashlib
import re

site = Path(__file__).resolve().parents[1] / 'project-page'
page = site / 'index.html'

def version(match):
    url = match.group(1)
    asset = site / url.removeprefix('./')
    if not asset.is_file():
        raise FileNotFoundError(asset)
    return f'{url}?v={hashlib.sha256(asset.read_bytes()).hexdigest()[:12]}'

source = page.read_text(encoding='utf-8')
source = re.sub(r'(\./assets/[^\s"\'<>?]+\.(?:css|js))(?:\?v=[a-f0-9]+)?', version, source)
page.write_text(source, encoding='utf-8', newline='\n')
print('Versioned homepage assets only.')
