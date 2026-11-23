from flask import Blueprint, abort, current_app, render_template, request

from .config import SOURCES, SPEC
from .errors import InspectionError
from .input import sample
from .modules.archive.service import archive
from .modules.archive.storage import get, recent
from .modules.certificate.service import certify
from .modules.settlement.service import settle
from .modules.settlement.storage import get as get_settlement, recent as recent_settlements
from .pipeline import run
from .modules.calibration.contract import FIELDS
from .views import CODES, KINDS, LABELS, STATES, half_mm_metres, metres, trace_frames, yuan

console = Blueprint('console', __name__)
console.add_app_template_filter(metres, 'metres')
console.add_app_template_filter(half_mm_metres, 'half_mm_metres')
console.add_app_template_filter(yuan, 'yuan')


def material(mode):
    if mode == 'custom':
        return {'traces':{key:request.form.get(key,'') for key in SOURCES},
                'settings':{key:request.form.get(key,'') for key in FIELDS}}
    return sample(mode)


@console.route('/', methods=['GET','POST'])
@console.route('/inspect/<section>', methods=['GET','POST'])
def view(section='design'):
    if section not in LABELS:
        abort(404)
    mode = request.values.get('sample','dirty')
    database = current_app.config['DATABASE']
    evidence, frames, entries, data = None, {}, [], {}
    issued, fresh, error, sources = None, False, None, {}
    settlements, statement, settled_now = [], None, False
    try:
        sources = material(mode)
        if section in ('archive','certificate','settlement'):
            if section == 'archive' and request.method == 'POST':
                issued, fresh = archive(database, sources)
            if section == 'settlement':
                if request.method == 'POST':
                    statement, fresh = settle(database, request.form.get('period'), request.form.get('unit_price'))
                    settled_now = True
                settlements = recent_settlements(database)
                if statement is None:
                    chosen = request.args.get('period') or (settlements[0]['period'] if settlements else None)
                    if chosen:
                        statement = get_settlement(database, chosen)
            entries = recent(database)
            identifier = request.args.get('inspection') or (entries[0]['inspection_id'] if entries else None)
            if section == 'certificate' and identifier:
                evidence = certify(get(database, identifier))
        else:
            data = run(sources, section)
            if 'calibration' in data:
                frames = trace_frames(data['calibration'])
    except InspectionError as exc:
        error = exc.code
    return render_template('console.html', section=section, labels=LABELS, codes=CODES, spec=SPEC, mode=mode,
                           data=data, frames=frames, error=error, materials=sources, entries=entries,
                           evidence=evidence, issued=issued, fresh=fresh, fields=FIELDS,
                           states=STATES, kinds=KINDS, settlements=settlements, statement=statement,
                           settled_now=settled_now)
