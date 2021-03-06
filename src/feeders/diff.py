from . import diffcommon, base
import bs4, difflib
from urllib.parse import urljoin

class Feeder(base.Feeder):
    def __iter__(self):
        for n, i in self.feeds.items():
            assert 'url' in i
            url = i['url']
            if url in self.resource:
                old = self.resource[url].splitlines()
                oldurl = url
            else:
                old = []
                oldurl = '/dev/null'
            try:
                soup = bs4.BeautifulSoup(base.download(url).strip(), 'html.parser')
                base_elem = soup.find("base")
                base_url = url if base_elem is None else base_elem["href"]
                for a in soup.find_all("a"):
                    if a["href"] is None: continue
                    abs_href = urljoin(base_url, a["href"])
                    a.append(" (" + abs_href + ")")
                new = soup.get_text().splitlines()
            except Exception as e:
                yield Exception('Error while loading %(url)s: %(err)s' % {
                    'url':url,
                    'err':e,
                })
                continue
            lines = list(difflib.unified_diff(old, new, fromfile=oldurl,
                tofile=url, lineterm=''))
            if i.get('ignore_white_space', 'yes').lower() == 'yes':
                lines = list(diffcommon.suppress_whitespace(lines))
            if len(lines) > 2:
                content = '\n'.join(lines)
                yield base.Entry(n, '%s changes' % url, content)
            self.resource[url] = '\n'.join(new)
            yield base.SyncRequest()
