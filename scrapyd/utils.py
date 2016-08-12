import sys
import os
from contextlib import suppress
from .sqlite import JsonSqliteDict
from subprocess import Popen, PIPE
from six import iteritems
from six.moves.configparser import *

import json
from twisted.web import resource

from scrapyd.spiderqueue import SqliteSpiderQueue
from scrapy.utils.python import to_bytes, to_unicode, to_native_str
from scrapyd.config import Config


class JsonResource(resource.Resource):

    json_encoder = json.JSONEncoder()

    def render(self, txrequest):
        r = resource.Resource.render(self, txrequest)
        return self.render_object(r, txrequest)

    def render_object(self, obj, txrequest):
        r = self.json_encoder.encode(obj) + "\n"
        txrequest.setHeader('Content-Type', 'application/json')
        txrequest.setHeader('Access-Control-Allow-Origin', '*')
        txrequest.setHeader('Access-Control-Allow-Methods', 'GET, POST, PATCH, PUT, DELETE')
        txrequest.setHeader('Access-Control-Allow-Headers',' X-Requested-With')
        txrequest.setHeader('Content-Length', len(r))
        return r.encode('utf-8')

class UtilsCache:
    # array of project name that need to be invalided
    invalid_cached_projects = []

    def __init__(self):
        self.cache_manager = JsonSqliteDict(table="utils_cache_manager")

    # Invalid the spider's list's cache of a given project (by name)
    @staticmethod
    def invalid_cache(project):
        invalid_projs=UtilsCache.invalid_cached_projects
        if len(invalid_projs) >=0 and project not in invalid_projs:
            invalid_projs.append(project)

    def __getitem__(self, key):
        if len(UtilsCache.invalid_cached_projects)>0:
            for p in UtilsCache.invalid_cached_projects:
                if p in self.cache_manager:
                    del self.cache_manager[p]
            UtilsCache.invalid_cached_projects[:] = []
        return self.cache_manager[key]

    def __setitem__(self, key, value):
        self.cache_manager[key] = value

def get_spider_queues(config):
    """Return a dict of Spider Queues keyed by project name"""
    dbsdir = config.get('dbs_dir', 'dbs')
    if not os.path.exists(dbsdir):
        os.makedirs(dbsdir)
    d = {}
    for project in get_project_list(config):
        dbpath = os.path.join(dbsdir, '%s.db' % project)
        d[project] = SqliteSpiderQueue(dbpath)
    return d

def get_project_list(config):
    """Get list of projects by inspecting the eggs dir and the ones defined in
    the scrapyd.conf [settings] section
    """
    eggs_dir = config.get('eggs_dir', 'eggs')
    if os.path.exists(eggs_dir):
        projects = os.listdir(eggs_dir)
    else:
        projects = []
    try:
        projects += [x[0] for x in config.cp.items('settings')]
    except NoSectionError:
        pass
    return projects

def native_stringify_dict(dct_or_tuples, encoding='utf-8', keys_only=True):
    """Return a (new) dict with unicode keys (and values when "keys_only" is
    False) of the given dict converted to strings. `dct_or_tuples` can be a
    dict or a list of tuples, like any dict constructor supports.
    """
    d = {}
    for k, v in iteritems(dict(dct_or_tuples)):
        k = to_native_str(k, encoding)
        if not keys_only:
            v = to_native_str(v, encoding)
        d[k] = v
    return d

def get_crawl_args(message):
    """Return the command-line arguments to use for the scrapy crawl process
    that will be started for this message
    """
    msg = message.copy()
    args = [to_native_str(msg['_spider'])]
    del msg['_project'], msg['_spider']
    settings = msg.pop('settings', {})
    for k, v in native_stringify_dict(msg, keys_only=False).items():
        args += ['-a']
        args += ['%s=%s' % (k, v)]
    for k, v in native_stringify_dict(settings, keys_only=False).items():
        args += ['-s']
        args += ['%s=%s' % (k, v)]
    return args

def get_spider_list(project, runner=None, pythonpath=None, version=''):
    """Return the spider list from the given project, using the given runner"""
    try:
        cache=getattr(get_spider_list,"cache")
    except AttributeError:
        get_spider_list.cache = UtilsCache()
    with suppress(KeyError):
        versionCache= get_spider_list.cache[project]
        spiders = versionCache[version]
        return spiders
    if runner is None:
        runner = Config().get('runner')
    env = os.environ.copy()
    env['PYTHONIOENCODING'] = 'UTF-8'
    env['SCRAPY_PROJECT'] = project
    if pythonpath:
        env['PYTHONPATH'] = pythonpath
    if version:
        env['SCRAPY_EGG_VERSION'] = version
    pargs = [sys.executable, '-m', runner, 'list']
    proc = Popen(pargs, stdout=PIPE, stderr=PIPE, env=env)
    out, err = proc.communicate()
    print (err)
    if proc.returncode:
        msgUnicode=to_unicode(err)
        if '[Errno 2]' in msgUnicode:
            if '' is version:
                return 'The project - {} - does not exist'.format(project)
            else:
                return 'The requested version - {} - and/or the project - {} - does not exist'.format(version, project)
        else: 
            msg = err or out or 'unknown error'
            raise RuntimeError(msg.splitlines()[-1])
    # FIXME: can we reliably decode as UTF-8?
    # scrapy list does `print(list)`
    tmp = out.decode('utf-8').splitlines();
    try:
        project_cache = get_spider_list.cache[project]
        project_cache[version] = tmp
    except KeyError:
        project_cache = {version: tmp}
    get_spider_list.cache[project] = project_cache
    return tmp
