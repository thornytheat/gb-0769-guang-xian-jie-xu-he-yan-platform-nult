from flask import Blueprint, current_app, jsonify, request

from .errors import InspectionError
from .input import request_material
from .modules.archive.service import archive
from .modules.archive.storage import get, recent
from .modules.certificate.service import certify
from .modules.settlement.service import settle
from .modules.settlement.storage import get as get_settlement, recent as recent_settlements
from .pipeline import run

api = Blueprint('fiber_api', __name__, url_prefix='/api')


def calculate(stage):
    return run(request_material(), stage)[stage]


@api.post('/trace')
def trace():
    return calculate('trace')


@api.post('/calibration')
def calibration():
    return calculate('calibration')


@api.post('/pairs')
def pairs():
    return calculate('pairs')


@api.post('/loss')
def loss():
    return calculate('loss')


@api.post('/design')
def design():
    return calculate('design')


@api.post('/inspections')
def submit():
    report, fresh = archive(current_app.config['DATABASE'], request_material())
    return {'inspection_id':report['inspection_id'], 'created':fresh, 'record':report}, 201 if fresh else 200


@api.get('/inspections')
def list_inspections():
    return {'inspections':recent(current_app.config['DATABASE'])}


@api.get('/inspections/<identifier>')
def inspection(identifier):
    return get(current_app.config['DATABASE'], identifier)


@api.get('/certificate/<identifier>')
def certificate(identifier):
    return certify(inspection(identifier))


@api.post('/settlements')
def create_settlement():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise InspectionError('JSON_OBJECT_REQUIRED', status=400)
    document, fresh = settle(current_app.config['DATABASE'], body.get('period'), body.get('unit_price'))
    return {'period': document['period'], 'created': fresh, 'settlement': document}, 201 if fresh else 200


@api.get('/settlements')
def list_settlements():
    return {'settlements': recent_settlements(current_app.config['DATABASE'])}


@api.get('/settlements/<period>')
def settlement(period):
    return get_settlement(current_app.config['DATABASE'], period)


@api.get('/export/<identifier>')
def export(identifier):
    response = jsonify(certificate(identifier))
    response.headers['Content-Disposition'] = f'attachment; filename="splice-{identifier[:12]}.json"'
    return response
