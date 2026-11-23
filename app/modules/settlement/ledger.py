from app.modules.archive.identity import canonical, digest

# 同一线路 = 同一组校准参数（全长、起偏、容差、参考损耗）。补测沿用原参数，
# 只换轨迹原件，因此留档记录可按校准参数归并到同一条线路。
def line_key(record):
    return digest(canonical(record['settings']))


def replay(documents):
    """按结算期顺序重放已结合同，还原每条线路每个接头的付款状态。

    payment 为当前生效的付款记录（原签发件 + 金额），扣回后置空；
    last_payment 保留最近一次付款，供改判回补时追溯原签发件和付款记录。
    """
    state = {}
    for document in sorted(documents, key=lambda doc: doc['period']):
        for line in document['lines']:
            joints = state.setdefault(line['line'], {})
            for row in line['joints']:
                joints.setdefault(row['joint'], {'seen': False, 'payment': None, 'last_payment': None})['seen'] = True
            for entry in line['entries']:
                joint = joints[entry['joint']]
                if entry['kind'] in ('PAYABLE', 'SUPPLEMENT'):
                    payment = {'period': document['period'], 'inspection_id': line['inspection_id'],
                               'amount_fen': entry['amount_fen']}
                    joint.update(payment=payment, last_payment=payment)
                else:
                    joint['payment'] = None
    return state


def compute(period, unit_price_fen, records, prior_documents, settled_at, rule):
    """按采信签发件核对每条线路每个接头本期该付多少。

    records 为全部留档核验（按留档先后排列），每条线路采信最新一份；
    已结月份由 prior_documents 重放还原，本期只补差额，不重复计款。
    """
    state = replay(prior_documents)
    by_line = {}
    for record in records:
        by_line.setdefault(line_key(record), []).append(record)
    lines = []
    for key in sorted(by_line):
        basis = by_line[key][-1]
        prior = state.get(key, {})
        joints, entries, listed = [], [], set()
        for row in basis['computed']['design']['rows']:
            joint = row['joint']
            listed.add(joint)
            payment = prior.get(joint, {}).get('payment')
            detail = {'joint': joint, 'code': row['code'], 'net_db': row['net_db']}
            if row['code'] == 'PASS':
                if payment:
                    joints.append({**detail, 'state': 'KEPT', 'amount_fen': 0})
                    continue
                previous = prior.get(joint)
                kind = 'SUPPLEMENT' if previous and previous['seen'] else 'PAYABLE'
                origin = previous['last_payment'] if kind == 'SUPPLEMENT' and previous else None
                entries.append({'joint': joint, 'kind': kind, 'amount_fen': unit_price_fen, 'origin': origin})
                joints.append({**detail, 'state': kind, 'amount_fen': unit_price_fen})
            elif payment:
                entries.append({'joint': joint, 'kind': 'DEDUCT', 'amount_fen': -payment['amount_fen'],
                                'origin': payment})
                joints.append({**detail, 'state': 'DEDUCT', 'amount_fen': -payment['amount_fen']})
            else:
                joints.append({**detail, 'state': 'UNPAID', 'amount_fen': 0})
        for joint in sorted(set(prior) - listed):
            payment = prior[joint]['payment']
            if payment:
                entries.append({'joint': joint, 'kind': 'DEDUCT', 'amount_fen': -payment['amount_fen'],
                                'origin': payment})
                joints.append({'joint': joint, 'code': None, 'net_db': None,
                               'state': 'DEDUCT', 'amount_fen': -payment['amount_fen']})
        if not joints and not entries:
            continue
        payable = sum(entry['amount_fen'] for entry in entries if entry['amount_fen'] > 0)
        deducted = sum(entry['amount_fen'] for entry in entries if entry['amount_fen'] < 0)
        lines.append({'line': key, 'inspection_id': basis['inspection_id'], 'generated_at': basis['generated_at'],
                      'joints': joints, 'entries': entries,
                      'payable_fen': payable, 'deducted_fen': deducted, 'net_fen': payable + deducted})
    totals = {'lines': len(lines),
              'payable_fen': sum(line['payable_fen'] for line in lines),
              'deducted_fen': sum(line['deducted_fen'] for line in lines),
              'net_fen': sum(line['net_fen'] for line in lines),
              'paid_joints': sum(1 for line in lines for entry in line['entries'] if entry['amount_fen'] > 0),
              'deducted_joints': sum(1 for line in lines for entry in line['entries'] if entry['amount_fen'] < 0)}
    return {'period': period, 'rule': rule, 'unit_price_fen': unit_price_fen, 'settled_at': settled_at,
            'lines': lines, 'totals': totals,
            'prior_periods': [doc['period'] for doc in sorted(prior_documents, key=lambda doc: doc['period'])]}
