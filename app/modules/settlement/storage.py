import json
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path

from app.errors import InspectionError
from app.modules.archive.identity import canonical

INSPECTIONS = 'CREATE TABLE IF NOT EXISTS inspections (id TEXT UNIQUE NOT NULL, document BLOB NOT NULL)'
SETTLEMENTS = 'CREATE TABLE IF NOT EXISTS settlements (period TEXT PRIMARY KEY, document BLOB NOT NULL)'


@contextmanager
def session(database):
    Path(database).parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database, timeout=15)) as connection, connection:
        connection.execute(INSPECTIONS)
        connection.execute(SETTLEMENTS)
        yield connection


@contextmanager
def locked(database):
    """结算写入全程持有写锁：重放已结月份、采信留档、落账三步不可被并发结算切开。"""
    Path(database).parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database, timeout=15)) as connection:
        connection.isolation_level = None
        connection.execute('BEGIN IMMEDIATE')
        with connection:
            connection.execute(INSPECTIONS)
            connection.execute(SETTLEMENTS)
            yield connection


def fetch(db, period):
    row = db.execute('SELECT document FROM settlements WHERE period=?', (period,)).fetchone()
    return json.loads(row[0]) if row else None


def all_documents(db):
    return [json.loads(data) for data, in db.execute('SELECT document FROM settlements ORDER BY period')]


def archived(db):
    return [json.loads(data) for data, in db.execute('SELECT document FROM inspections ORDER BY rowid')]


def insert(db, document):
    db.execute('INSERT INTO settlements VALUES (?, ?)', (document['period'], canonical(document)))


def get(database, period):
    with session(database) as db:
        document = fetch(db, period)
        if not document:
            raise InspectionError('SETTLEMENT_NOT_FOUND', status=404)
        return document


def summary(document):
    return {'period': document['period'], 'unit_price_fen': document['unit_price_fen'],
            'settled_at': document['settled_at'], **document['totals']}


def recent(database):
    with session(database) as db:
        rows = (json.loads(data) for data, in db.execute('SELECT document FROM settlements ORDER BY period DESC LIMIT 50'))
        return [summary(row) for row in rows]
