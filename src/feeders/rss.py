import base, rsscommon

class Feeder(base.Feeder):
    def __iter__(self):
        for n, i in self.feeds.items():
            assert 'url' in i
            url = i['url']
            seen = self.resource.get(url, set())
            try:
                entries = rsscommon.get_entries(url)
                for e in entries:
                    id = rsscommon.get_id(e)
                    if id not in seen:
                        yield base.Entry(n, e.title, \
                           '<p><b>%(title)s</b><br/><font size="-1"><a href="%(link)s">%(link)s</a></font></p>%(content)s<a href="%(extralink)s">%(extralink)s</a>' % {
                               'title':e.title,
                               'link':e.link,
                               'content':rsscommon.get_content(e),
                               'extralink':rsscommon.get_extra_link(e),
                           }, date=rsscommon.get_date(e), html=True)
                        seen.add(id)
                self.resource[url] = seen
            except Exception as e:
                raise Exception('Error from feed %(name)s: %(err)s' % {
                    'name':n,
                    'err':e,
                })
