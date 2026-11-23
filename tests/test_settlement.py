import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.main import create_app
from app.modules.archive.service import archive as store
from app.modules.settlement.service import settle


def archive(client, data):
    return client.post('/api/inspections', json=data).json['inspection_id']


def remeasurement(dirty, a03='0.05', b01='0.55'):
    """同一线路的补测：校准参数不变，只换轨迹原件，得到新的留档记录。"""
    data = {**dirty, 'traces': dict(dirty['traces'])}
    data['traces']['a'] = data['traces']['a'].replace('A-03,400,m,0.33', f'A-03,400,m,{a03}')
    data['traces']['b'] = data['traces']['b'].replace('B-01,943,m,0.30', f'B-01,943,m,{b01}')
    return data


def test_unarchived_measurements_stay_off_books(client):
    client.post('/api/loss', json={'sample': 'dirty'})
    r = client.post('/api/settlements', json={'period': '2026-10', 'unit_price': '180.00'})
    assert r.status_code == 201 and r.json['created'] is True
    document = r.json['settlement']
    assert document['lines'] == [] and document['totals']['net_fen'] == 0


def test_only_signed_pass_joints_are_payable(client, dirty):
    basis = archive(client, dirty)
    r = client.post('/api/settlements', json={'period': '2026-10', 'unit_price': '180.00'})
    document = r.json['settlement']
    (line,) = document['lines']
    assert line['inspection_id'] == basis
    states = {row['joint']: row['state'] for row in line['joints']}
    assert states == {'F-01': 'PAYABLE', 'F-02': 'PAYABLE', 'F-03': 'UNPAID',
                      'F-04': 'PAYABLE', 'F-05': 'UNPAID', 'F-06': 'UNPAID'}
    assert document['totals']['net_fen'] == 3 * 18000
    assert all(entry['kind'] == 'PAYABLE' and entry['origin'] is None for entry in line['entries'])


def test_remeasurement_reversals_trace_to_original_payment(client, dirty):
    first = archive(client, dirty)
    client.post('/api/settlements', json={'period': '2026-10', 'unit_price': '180.00'})
    second = archive(client, remeasurement(dirty))
    assert second != first
    document = client.post('/api/settlements', json={'period': '2026-11', 'unit_price': '180.00'}).json['settlement']
    (line,) = document['lines']
    assert line['inspection_id'] == second
    entries = {entry['joint']: entry for entry in line['entries']}
    assert entries['F-01']['kind'] == 'DEDUCT' and entries['F-01']['amount_fen'] == -18000
    assert entries['F-01']['origin'] == {'period': '2026-10', 'inspection_id': first, 'amount_fen': 18000}
    assert entries['F-03']['kind'] == 'SUPPLEMENT' and entries['F-03']['amount_fen'] == 18000
    assert entries['F-03']['origin'] is None
    states = {row['joint']: row['state'] for row in line['joints']}
    assert states['F-02'] == states['F-04'] == 'KEPT'
    assert document['totals']['net_fen'] == 0
    assert document['prior_periods'] == ['2026-10']


def test_settled_period_ignores_late_records(client, dirty):
    first = archive(client, dirty)
    original = client.post('/api/settlements', json={'period': '2026-10', 'unit_price': '180.00'}).json['settlement']
    archive(client, remeasurement(dirty))
    again = client.post('/api/settlements', json={'period': '2026-10', 'unit_price': '999.00'})
    assert again.status_code == 200 and again.json['created'] is False
    assert again.json['settlement'] == original
    assert again.json['settlement']['lines'][0]['inspection_id'] == first


def test_retry_settlement_never_double_counts(client, dirty):
    archive(client, dirty)
    first = client.post('/api/settlements', json={'period': '2026-10', 'unit_price': '180.00'})
    retry = client.post('/api/settlements', json={'period': '2026-10', 'unit_price': '180.00'})
    assert (first.status_code, retry.status_code) == (201, 200)
    assert first.json['settlement'] == retry.json['settlement']
    assert len(client.get('/api/settlements').json['settlements']) == 1
    document = client.post('/api/settlements', json={'period': '2026-11', 'unit_price': '180.00'}).json['settlement']
    assert document['totals']['net_fen'] == 0
    assert all(row['state'] in ('KEPT', 'UNPAID') for row in document['lines'][0]['joints'])


def test_supplement_after_deduction_traces_original_payment(client, dirty):
    first = archive(client, dirty)
    client.post('/api/settlements', json={'period': '2026-10', 'unit_price': '180.00'})
    archive(client, remeasurement(dirty))
    client.post('/api/settlements', json={'period': '2026-11', 'unit_price': '180.00'})
    third = archive(client, remeasurement(dirty, b01='0.28'))
    document = client.post('/api/settlements', json={'period': '2026-12', 'unit_price': '180.00'}).json['settlement']
    (line,) = document['lines']
    assert line['inspection_id'] == third
    entries = {entry['joint']: entry for entry in line['entries']}
    assert entries['F-01']['kind'] == 'SUPPLEMENT' and entries['F-01']['amount_fen'] == 18000
    assert entries['F-01']['origin'] == {'period': '2026-10', 'inspection_id': first, 'amount_fen': 18000}


def test_deduction_reverses_original_paid_amount(client, dirty):
    archive(client, dirty)
    client.post('/api/settlements', json={'period': '2026-10', 'unit_price': '100.00'})
    archive(client, remeasurement(dirty))
    document = client.post('/api/settlements', json={'period': '2026-11', 'unit_price': '200.00'}).json['settlement']
    entries = {entry['joint']: entry for entry in document['lines'][0]['entries']}
    assert entries['F-01']['amount_fen'] == -10000
    assert entries['F-03']['amount_fen'] == 20000


def test_closed_period_cannot_be_resettled(client, dirty):
    archive(client, dirty)
    client.post('/api/settlements', json={'period': '2026-11', 'unit_price': '180.00'})
    r = client.post('/api/settlements', json={'period': '2026-10', 'unit_price': '180.00'})
    assert (r.status_code, r.json['code']) == (409, 'SETTLEMENT_PERIOD_CLOSED')


@pytest.mark.parametrize('period', ['2026-13', '2026-1', '10-2026', '2026/10', '', None, 202610])
def test_bad_period_rejected(client, period):
    r = client.post('/api/settlements', json={'period': period, 'unit_price': '180.00'})
    assert (r.status_code, r.json['code']) == (422, 'BAD_SETTLEMENT_PERIOD')


@pytest.mark.parametrize('price', ['0', '-1', 'abc', '1.005', None, 0, 1.5, True])
def test_bad_price_rejected(client, price):
    r = client.post('/api/settlements', json={'period': '2026-10', 'unit_price': price})
    assert (r.status_code, r.json['code']) == (422, 'BAD_UNIT_PRICE')


def test_settlement_requires_json_object(client):
    r = client.post('/api/settlements', data='not json', content_type='text/plain')
    assert (r.status_code, r.json['code']) == (400, 'JSON_OBJECT_REQUIRED')


def test_unknown_settlement_returns_404(client):
    r = client.get('/api/settlements/2026-10')
    assert (r.status_code, r.json['code']) == (404, 'SETTLEMENT_NOT_FOUND')


def test_settlements_listing_and_detail(client, dirty):
    archive(client, dirty)
    client.post('/api/settlements', json={'period': '2026-10', 'unit_price': '180.00'})
    listing = client.get('/api/settlements').json['settlements']
    assert [row['period'] for row in listing] == ['2026-10']
    assert listing[0]['net_fen'] == 54000 and listing[0]['paid_joints'] == 3
    detail = client.get('/api/settlements/2026-10').json
    assert detail['totals']['net_fen'] == 54000 and detail['unit_price_fen'] == 18000


def test_competing_settlements_publish_once(tmp_path, dirty):
    database = tmp_path / 'db'
    store(database, dirty)
    with ThreadPoolExecutor(max_workers=5) as executor:
        results = list(executor.map(lambda _: settle(database, '2026-10', 18000), range(10)))
    assert sum(fresh for _, fresh in results) == 1
    assert len({json.dumps(document, sort_keys=True) for document, _ in results}) == 1


def test_settlement_page_renders(client, dirty):
    assert client.get('/inspect/settlement').status_code == 200
    archive(client, dirty)
    response = client.post('/inspect/settlement', data={'period': '2026-10', 'unit_price': '180.00'})
    assert response.status_code == 200
    page = client.get('/inspect/settlement')
    assert '进度款结算'.encode() in page.data and b'F-01' in page.data
    assert '540.00'.encode() in page.data and '本期应付'.encode() in page.data


def test_settlement_page_shows_adjustments(client, dirty):
    archive(client, dirty)
    client.post('/api/settlements', json={'period': '2026-10', 'unit_price': '180.00'})
    archive(client, remeasurement(dirty))
    client.post('/api/settlements', json={'period': '2026-11', 'unit_price': '180.00'})
    page = client.get('/inspect/settlement?period=2026-11')
    assert '本期扣回'.encode() in page.data and '本期补付'.encode() in page.data
    assert '-180.00'.encode() in page.data
