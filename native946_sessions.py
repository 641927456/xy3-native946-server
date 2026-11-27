"""One live native owner per character; independent of gameplay reloads."""
from pathlib import Path
from threading import RLock
import weakref

from local_accounts import AccountError


class CharacterLeases:
    def __init__(self):
        self.lock=RLock();self.owners={}

    @staticmethod
    def database_key(database):return str(Path(database).resolve()).casefold()

    def claim(self,database,cid,owner):
        key=(self.database_key(database),cid)
        with self.lock:
            existing=self.owners.get(key)
            if existing is not None and existing() is not None and existing() is not owner:
                raise AccountError(409,'这个角色已经在线，原连接继续保留。')
            def remove(reference):
                with self.lock:
                    if self.owners.get(key) is reference:self.owners.pop(key,None)
            self.owners[key]=weakref.ref(owner,remove)

    def release_port(self,database,port):
        name=self.database_key(database)
        with self.lock:
            for key,reference in list(self.owners.items()):
                owner=reference()
                if owner is None or (key[0]==name and getattr(owner,'source_port',None)==port):
                    self.owners.pop(key,None)


LEASES=CharacterLeases()
