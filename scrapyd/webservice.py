import traceback
import uuid
try:
    from cStringIO import StringIO as BytesIO
except ImportError:
    from io import BytesIO

from twisted.python import log

from .utils import get_spider_list, JsonResource, UtilsCache
from scrapy.utils.python import to_bytes, to_unicode, to_native_str

class WsResource(JsonResource):

    def __init__(self, root):
        JsonResource.__init__(self)
        self.root = root

    def render(self, txrequest):
        try:
            return JsonResource.render(self, txrequest)
        except Exception as e:
            if self.root.debug:
                return traceback.format_exc()
            log.err()
            r = {"node_name": self.root.nodename, "status": "error", "message": str(e)}
            return self.render_object(r, txrequest)

class DaemonStatus(WsResource):

    def render_GET(self, txrequest):
        pending = sum(q.count() for q in self.root.poller.queues.values())
        running = len(self.root.launcher.processes)
        finished = len(self.root.launcher.finished)

        return {"node_name": self.root.nodename, "status":"ok", "pending": pending, "running": running, "finished": finished}


class Schedule(WsResource):

    def render_POST(self, txrequest):
        settings = txrequest.args.pop(b'setting', [])
        settings = dict(to_unicode(x).split('=', 1) for x in settings)
        args = dict((to_unicode(k), to_unicode(v[0])) for k, v in txrequest.args.items())
        project = to_unicode(args.pop('project'))
        spider = to_unicode(args.pop('spider'))
        version = to_unicode(args.get('_version', ''))
        spiders = get_spider_list(project, version=version)
        if not spider in spiders:
            return {"status": "error", "message": "spider '%s' not found" % spider}
        args['settings'] = settings
        jobid = args.pop(b'jobid', uuid.uuid1().hex)
        args['_job'] = jobid
        self.root.scheduler.schedule(project, spider, **args)
        return {"node_name": self.root.nodename, "status": "ok", "jobid": jobid}

class Cancel(WsResource):

    def render_POST(self, txrequest):
        args = dict((k, v[0]) for k, v in txrequest.args.items())
        project = to_unicode(args[b'project'])
        jobid = to_unicode(args[b'job'])
        signal = args.get('signal', 'TERM')
        prevstate = None
        queue = self.root.poller.queues[project]
        c = queue.remove(lambda x: x["_job"] == jobid)
        if c:
            prevstate = "pending"
        spiders = self.root.launcher.processes.values()
        for s in spiders:
            if s.job == jobid:
                s.transport.signalProcess(signal)
                prevstate = "running"
        return {"node_name": self.root.nodename, "status": "ok", "prevstate": prevstate}

class AddVersion(WsResource):

    def render_POST(self, txrequest):
        project = to_unicode(txrequest.args[b'project'][0])
        version = to_unicode(txrequest.args[b'version'][0])
        eggf = BytesIO(txrequest.args[b'egg'][0])
        self.root.eggstorage.put(eggf, project, version)
        spiders = get_spider_list(project, version=version)
        self.root.update_projects()
        UtilsCache.invalid_cache(project)
        return {"node_name": self.root.nodename, "status": "ok", "project": project, "version": version, \
            "spiders": len(spiders)}

class ListProjects(WsResource):

    def render_GET(self, txrequest):
        projects = self.root.scheduler.list_projects()
        projectlist=list(projects)
        return {"node_name": self.root.nodename, "status": "ok", "projects": projectlist}

class ListVersions(WsResource):

    def render_GET(self, txrequest):
        project = to_unicode(txrequest.args[b'project'][0])
        versions = self.root.eggstorage.list(project)
        if not versions or len(versions)==0:
            versions = "No versions exist for project %s"  % project
        return {"node_name": self.root.nodename, "status": "ok", "versions": versions}

class ListSpiders(WsResource):

    def render_GET(self, txrequest):
        project = to_unicode(txrequest.args[b'project'][0])
        version = to_unicode(txrequest.args.get(b'_version', [''])[0])
        spiders = get_spider_list(project, runner=self.root.runner, version=version)
        return {"node_name": self.root.nodename, "status": "ok", "spiders": spiders}

class ListJobs(WsResource):

    def render_GET(self, txrequest):
        project = to_unicode(txrequest.args[b'project'][0])
        spiders = self.root.launcher.processes.values()
        running = [{"id": s.job, "spider": s.spider,
            "start_time": s.start_time.isoformat(' ')} for s in spiders if s.project == project]
        try:
            queue = self.root.poller.queues[project]
            pending = [{"id": x["_job"], "spider": x["name"]} for x in queue.list()]
        except KeyError:
            pending=[]
        finished = [{"id": s.job, "spider": s.spider,
            "start_time": s.start_time.isoformat(' '),
            "end_time": s.end_time.isoformat(' ')} for s in self.root.launcher.finished
            if s.project == project]
        return {"node_name": self.root.nodename, "status":"ok", "pending": pending, "running": running, "finished": finished}

class DeleteProject(WsResource):

    def render_POST(self, txrequest):
        project = to_unicode(txrequest.args[b'project'][0])
        self._delete_version(project)
        UtilsCache.invalid_cache(project)
        return {"node_name": self.root.nodename, "status": "ok"}

    def _delete_version(self, project, version=None):
        self.root.eggstorage.delete(project, version)
        self.root.update_projects()

class DeleteVersion(DeleteProject):

    def render_POST(self, txrequest):
        project = to_unicode(txrequest.args[b'project'][0])
        version = to_unicode(txrequest.args[b'version'][0])
        self._delete_version(project, version)
        UtilsCache.invalid_cache(project)
        return {"node_name": self.root.nodename, "status": "ok"}
