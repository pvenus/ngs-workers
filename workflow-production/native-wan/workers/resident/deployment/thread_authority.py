"""One Request-service DB connection per thread, without SQLite policy bypass.

The supplied factory must open the approved existing authority, with its source
and schema guards. The proxy opens nothing outside an explicit connection scope.
It does not change check_same_thread, migrate a DB, or spawn an operating server.
"""
from contextlib import contextmanager
from threading import local

class ThreadAuthority:
    def __init__(self,factory):
        if not callable(factory):raise ValueError('AUTHORITY_FACTORY_REQUIRED')
        self._factory=factory;self._local=local()
    def _current(self):
        if not getattr(self._local,'depth',0):raise RuntimeError('AUTHORITY_SCOPE_REQUIRED')
        return self._local.authority
    def __getattr__(self,name):
        if name.startswith('_'):raise AttributeError(name)
        return getattr(self._current(),name)
    @contextmanager
    def scope(self):
        depth=getattr(self._local,'depth',0)
        if depth==0:self._local.authority=self._factory()
        self._local.depth=depth+1
        try:yield self
        finally:
            self._local.depth-=1
            if self._local.depth==0:
                authority=self._local.authority
                del self._local.authority
                close=getattr(authority,'close',None)
                if callable(close):close()
                else:authority.db.close()

def scoped_handler(handler_type,authority):
    class ScopedHandler(handler_type):
        def handle(self):
            with authority.scope():return super().handle()
    return ScopedHandler

class ScopedLoop:
    def __init__(self,loop,authority):self.loop,self.authority=loop,authority;self.stop=loop.stop
    def serve(self):
        with self.authority.scope():return self.loop.serve()
    def tick(self):
        with self.authority.scope():return self.loop.tick()
