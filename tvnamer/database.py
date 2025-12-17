


from dataclasses import dataclass
from pathlib import Path
from sqlalchemy.orm import Session
from sqlalchemy import MetaData, Table, create_engine, delete
from sqlalchemy.dialects.sqlite import Insert as insert
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column

from sqlalchemy.orm import MappedAsDataclass

engine=None
session=None
metadata=None

def init_database(config):
    global engine, session, metadata
    Path(config['kvstore']).parent.mkdir(parents=True,exist_ok=True)
    engine = create_engine(f"sqlite:///{config['kvstore']}", echo=config['verbose'])
    session = Session(engine)
    metadata = MetaData()
    Base.metadata.create_all(engine)

# declarative base class
class Base(DeclarativeBase):
    pass



@dataclass
class KVStore(MappedAsDataclass,Base):
    __tablename__ = "series_location"

    fullfilename: Mapped[str]= mapped_column(primary_key=True, unique=True)
    seriesid: Mapped[str]
    season: Mapped[str]
    episode: Mapped[str]
    newfilename: Mapped[str] = mapped_column(index=True)
    def to_dict(self):
        temp={el.name:getattr(self,el.name) for el in self.__table__.columns}
        return {k: v for k, v in  temp.items() if v is not None}


from sqlalchemy import select

def lookup(fullfilename:str)->str|None:
    stmt = select(KVStore).where(KVStore.fullfilename==fullfilename)
    row = session.execute(stmt).one_or_none()
    return None if (row == None) else row[0].seriesid
def find_by_newname(newfilename:str):
    stmt = select(KVStore).where(KVStore.newfilename==newfilename)
    row = session.execute(stmt).one_or_none()
    return None if (row == None) else row

def upsert(fullfilename:str,seriesid:str|None=None,season:str|None=None,episode:str|None=None,newfilename:str|None=None):
    values={k: v for k, v in  locals().items() if v is not None}
    existing = session.execute(select(KVStore).where(KVStore.fullfilename==fullfilename)).one_or_none()
    if existing:
        values.update(existing[0].to_dict())
    stmt = insert(KVStore).values(values)
    stmt = stmt.on_conflict_do_update(index_elements=["fullfilename"],set_={k: stmt.excluded[k] for k,v in values.items()})
    session.execute(stmt)
    session.commit()

def forget(fullfilename:str)->None:
    stmt = delete(KVStore).where(KVStore.fullfilename==fullfilename)
    session.execute(stmt).fetchone()
