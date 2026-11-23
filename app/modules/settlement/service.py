import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from app.config import SPEC
from app.errors import InspectionError
from . import storage
from .ledger import compute

PERIOD = re.compile(r'^\d{4}-(0[1-9]|1[0-2])$')
MAX_FEN = 99_999_999


def parse_period(value):
    if not isinstance(value, str) or not PERIOD.match(value):
        raise InspectionError('BAD_SETTLEMENT_PERIOD')
    return value


def parse_price(value):
    if not isinstance(value, str) and type(value) is not int:
        raise InspectionError('BAD_UNIT_PRICE')
    try:
        fen = Decimal(str(value)) * 100
    except InvalidOperation:
        raise InspectionError('BAD_UNIT_PRICE') from None
    if fen != fen.to_integral_value() or not 0 < fen <= MAX_FEN:
        raise InspectionError('BAD_UNIT_PRICE')
    return int(fen)


def settle(database, period, unit_price):
    """月度结算：已结月份原样返回（重试不重复计款），新月份按采信签发件落账。

    晚到的留档记录不会改写已结月份；已结月份之前的月份不允许补结，
    以免改动后续月份的补扣链条。
    """
    period = parse_period(period)
    price_fen = parse_price(unit_price)
    with storage.locked(database) as db:
        existing = storage.fetch(db, period)
        if existing:
            return existing, False
        priors = storage.all_documents(db)
        if priors and period < priors[-1]['period']:
            raise InspectionError('SETTLEMENT_PERIOD_CLOSED', status=409)
        settled_at = datetime.now(timezone.utc).isoformat(timespec='seconds')
        document = compute(period, price_fen, storage.archived(db), priors, settled_at, SPEC)
        storage.insert(db, document)
        return document, True
