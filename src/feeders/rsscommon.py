'''
Functionality common to RSS-based feeders. This file is not intended to be
imported as a standalone feeder.
'''

import feedparser

def get_entries(url):
    return feedparser.parse(url).entries

def get_id(entry):
    try:
        return entry.id
    except:
        return entry.title