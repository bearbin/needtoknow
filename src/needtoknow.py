#!/usr/bin/env python3

import argparse, bz2, collections.abc, fcntl, importlib, json, logging, numbers, os, pickle, re, socket, sys, urllib.error, urllib.request
from output import sender
from feeders.base import SyncRequest

# How many times to attempt reconnecting to our output mailbox when the
# connection is dropped.
RECONNECT_ATTEMPTS = 3

def get_resource_path(root, name):
    return os.path.join(root, '%s.pickle.bz2' % name)

def construct_feeder(root, name, log):
    try:
        mod = importlib.import_module('feeders.%s' % name)
    except Exception as e:
        log.warning('failed to import feeder %s: %s' % (name, e))
        return None

    respath = get_resource_path(root, name)
    if os.path.exists(respath):
        with bz2.BZ2File(respath, 'rb') as f:
            resource = pickle.load(f)
    else:
        resource = {}

    return mod.Feeder(resource)

def online():
    try:
        urllib.request.urlopen('http://www.google.com', timeout=5)
        return True
    except urllib.error.URLError:
        return False

def main():
    parser = argparse.ArgumentParser('Monitor RSS and other feeds')
    parser.add_argument('--disable', '-d', action='append', default=[],
        help='disable a particular feeder by name')
    parser.add_argument('--enable', '-e', action='append', default=[],
        help='enable a particular feeder by name')
    parser.add_argument('--exclude', '-x', action='append', default=[],
        help='exclude a particular feed by name')
    parser.add_argument('--include', '-i', action='append', default=[],
        help='include a particular feed by name')
    parser.add_argument('--check-connection', action='store_true',
        help='check whether we have an internet connection first')
    parser.add_argument('--config', default=os.path.expanduser('~/.config/needtoknow'),
        help='configuration location')
    parser.add_argument('--data', default=os.path.expanduser('~/.local/share/needtoknow'),
        help='data location')
    # Actual default is feeds.json.
    parser.add_argument('--feeds', action='append', default=[],
        help='name of feeds file to check)')
    parser.add_argument('--dry-run', action='store_true',
        help='scan feeds but don\'t send any updates')
    parser.add_argument('--debug', action='store_true',
        help='instead of catching exceptions, let them dump a back trace')
    opts = parser.parse_args()
    if opts.feeds == []:
        opts.feeds = ["feeds.json"]

    log = logging.getLogger('needtoknow')
    log.addHandler(logging.StreamHandler(sys.stderr))
    # Determine whether we are running interactively.
    if sys.stdout.isatty():
        log.setLevel(logging.INFO)
    else:
        log.setLevel(logging.WARNING)

    # Check whether we're already running, to prevent multiple instances running
    # at once. Multiple running instances can interfere with each other when
    # saving state.
    me = open(os.path.abspath(__file__), 'rt')
    try:
        fcntl.flock(me, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log.error('needtoknow is already running; exiting\n')
        return -1

    if opts.check_connection and not online():
        log.info('no network connection; exiting')
        return 0

    try:
        with open(os.path.join(opts.config, 'conf.json')) as f:
            conf = json.load(f)
        if not isinstance(conf, collections.abc.Mapping):
            raise TypeError('configuration is not a JSON object')
        if not all(isinstance(v, (str, numbers.Integral))
                for v in conf.values()):
            raise TypeError('configuration values are not all strings or '
                'numbers')
    except Exception as e:
        if opts.debug:
            raise
        log.error('Failed to parse config: %s' % e)
        return -1

    feedlist = []
    for feeds in opts.feeds:
        try:
            with open(os.path.join(opts.config, feeds)) as f:
                feedlist.append(json.load(f))
            if not isinstance(feedlist[-1], collections.abc.Mapping):
                raise TypeError('feeds is not a JSON object')
            if not all(isinstance(v, collections.abc.Mapping)
                    for v in feedlist[-1].values()):
                raise TypeError('feed values are not all JSON objects')
        except Exception as e:
            if opts.debug:
                raise
            log.error('Failed to parse feeds: %s' % e)
            return -1

    feeders = {}

    log.info('Loading feeders...')
    for feeds in feedlist:
        for s, v in feeds.items():
            f = v.get('feeder')
            if f not in feeders:
                log.info(' Loading %s...' % f)
                feeders[f] = construct_feeder(opts.data, f, log)

            if feeders[f] is None:
                log.warning(' Warning: No feeder named %s (referenced by %s).' % (f, s))
            else:
                if s in opts.include or (opts.include == [] and s not in opts.exclude):
                    feeders[f].add(s, v)

    # Set timeout on socket connections to 60 seconds. Without this, some
    # platforms have no timeout (even on the kernel side) and socket connections
    # can stall forever.
    socket.setdefaulttimeout(60)

    out = sender.Sender(conf)
    try:
        out.connect()
    except Exception as e:
        if opts.debug:
            raise
        log.error('Failed to connect to mail server: %s' % e)
        return -1

    ret = 0

    log.info('Looking for updates...')
    for f in feeders:

        def commit_changes():
            if not opts.dry_run:
                log.info('  Committing resource changes...')
                respath = get_resource_path(opts.data, f)
                if not os.path.exists(os.path.dirname(respath)):
                    os.makedirs(os.path.dirname(respath))
                with bz2.BZ2File(respath, 'wb') as fobj:
                    pickle.dump(feeders[f].resource, fobj)

        if feeders[f] is None:
            # We failed to load this feeder.
            continue
        if f in opts.disable or (opts.enable != [] and f not in opts.enable):
            continue
        try:
            log.info(' Scanning %s...' % f)
            for entry in feeders[f]:

                if isinstance(entry, Exception):
                    if opts.debug:
                        raise entry
                    log.warning('  Feeder \'%s\' threw exception: %s' % (f, entry))
                    ret = -1
                    continue
                elif isinstance(entry, SyncRequest):
                    commit_changes()
                    continue

                # Check if we should discard this entry.
                assert entry.name in feeders[f].feeds, \
                    'feeder \'%s\' yielded entry from unknown feed \'%s\'' % \
                    (f, entry.name)
                skip = False
                for blacklist in feeders[f].feeds[entry.name].get('blacklist', []):
                    try:
                        if re.search(blacklist, entry.subject) is not None:
                            log.info('  Discarding \'[%s] %s\' as blacklisted' %
                                (entry.name, entry.subject))
                            skip = True
                            break
                    except Exception as e:
                        if opts.debug:
                            raise
                        log.error('  Failed to run regex blacklist \'%s\' '
                            'against %s: %s' % (blacklist, entry.name, e))
                        ret = -1
                if skip:
                    continue
                if opts.dry_run:
                    log.info('  skipping \'%s\' send due to --dry-run' % entry.subject)
                    continue
                try:
                    for i in range(RECONNECT_ATTEMPTS + 1):
                        try:
                            out.send(entry, log, i == 0)
                            break
                        except ConnectionResetError:
                            if i == RECONNECT_ATTEMPTS:
                                raise
                            log.info('  connection reset by peer; reconnecting...')
                        except (socket.timeout, TimeoutError):
                            if i == RECONNECT_ATTEMPTS:
                                raise
                            log.info('  socket timeout; reconnecting...')
                        out.connect()
                except Exception as e:
                    if opts.debug:
                        raise
                    log.error('  Failed to send update for %s \'%s\': %s' % (entry.name, entry.subject, e))
                    ret = -1
        except Exception as e:
            if opts.debug:
                raise
            log.warning('  Feeder \'%s\' threw exception: %s' % (f, e))

        commit_changes()

    out.disconnect()

    return ret

if __name__ == '__main__':
    sys.exit(main())
