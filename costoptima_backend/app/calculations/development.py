"""Read-only development snapshots and account-scoped, audited run artifacts.

This module adds no calculation engine, database or master store. A confirmation
binds an authenticated account to a short-lived hash of current relational masters;
calculation runs use that same transaction-wide read-only snapshot.
"""
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from uuid import UUID
import base64
import hmac
import json
import re
import time

from sqlalchemy import MetaData, Table, event, select
from sqlalchemy.orm import Session
from app import database
from app.config import get_settings
from app.masters import metadata as m

CONFIRMATION = 'development-only'
TOKEN_SECONDS = 600
METADATA_TABLES = (m.masters, m.families, m.sources, m.columns, m.merge_columns, m.output_columns)


def serialize(value):
    if isinstance(value, Decimal): return str(value)
    if isinstance(value, (date, datetime)): return value.isoformat()
    raise TypeError(type(value).__name__)


def canonical(value):
    return json.dumps(value, default=serialize, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def signature(value, purpose):
    return hmac.new(get_settings().csrf_signing_key.encode(),
                    purpose.encode() + b':' + canonical(value), 'sha256').hexdigest()


@contextmanager
def read_only_masters():
    """Use a dedicated plain Session, never the authentication/audit write session.

    MySQL enforces READ ONLY inside REPEATABLE READ. The SQL allowlist is defense
    in depth and also blocks accidental writes in isolated SQLite fixtures.
    Nothing changes the engine-wide isolation or account privileges.
    """
    engine = database.engine
    dialect = engine.dialect.name
    if dialect not in {'mysql', 'mariadb', 'sqlite'}:
        raise RuntimeError('This database does not support the approved development isolation')
    with engine.connect() as base:
        conn = base.execution_options(isolation_level='REPEATABLE READ') if dialect != 'sqlite' else base
        if dialect == 'sqlite':
            previous = conn.exec_driver_sql('PRAGMA query_only').scalar()
            conn.exec_driver_sql('PRAGMA query_only=ON')
        else:
            conn.exec_driver_sql('SET TRANSACTION READ ONLY')
        def check_read_only(connection, cursor, statement, parameters, context, many):
            sql = statement.lstrip()
            if not re.match(r'^(SELECT|SHOW|DESCRIBE)\b', sql, re.I) and not (
                    dialect == 'sqlite' and re.match(r'^PRAGMA (?:(?:main|temp)\.)?(?:table_xinfo|table_info|index_list|index_info|foreign_key_list)\(', sql, re.I)):
                raise RuntimeError('Development master access is read-only')
        event.listen(conn, 'before_cursor_execute', check_read_only)
        try:
            with Session(bind=conn, autoflush=False, expire_on_commit=False) as db:
                yield db
        finally:
            event.remove(conn, 'before_cursor_execute', check_read_only)
            conn.rollback()
            if dialect == 'sqlite':
                conn.exec_driver_sql(f'PRAGMA query_only={int(previous)}')
                conn.rollback()


def master_snapshot(db):
    """Hash every current source value and its actual configured field mappings.

    A fingerprint proves which values were used; it does not certify that held
    reconciliation differences or missing master values have been approved.
    """
    digest = sha256(); source_definitions = []; master_count = 0
    for table in METADATA_TABLES:
        rows = [dict(row) for row in db.execute(select(table).order_by(*table.primary_key.columns)).mappings()]
        digest.update(canonical({'metadata_table': table.name, 'rows': rows}))
        if table is m.sources: source_definitions = rows
        if table is m.masters: master_count = len(rows)
    total_rows = 0
    for definition in source_definitions:
        name = definition['data_table_name']
        if not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', name):
            raise RuntimeError('Configured source identifier is invalid')
        table = Table(name, MetaData(), autoload_with=db.connection())
        if 'row_id' not in table.c:
            raise RuntimeError('A master source is missing its row identity')
        digest.update(canonical({'source_table': name, 'schema': [
            {'name': col.name, 'type': str(col.type), 'nullable': col.nullable, 'primary_key': col.primary_key}
            for col in table.c]}))
        for row in db.execute(select(table).order_by(table.c.row_id)).mappings():
            digest.update(canonical(dict(row))); total_rows += 1
    return {'sha256': digest.hexdigest(), 'master_count': master_count,
            'source_count': len(source_definitions), 'source_rows': total_rows,
            'isolation': 'database-read-only', 'verification_status': 'development-unverified',
            'notice': 'Current master-data snapshot. Unresolved master values and calculation verification are shown in the results.'}


def issue_snapshot(user_id, snapshot):
    payload = {'account': int(user_id), 'sha256': snapshot['sha256'], 'expires_at': int(time.time()) + TOKEN_SECONDS}
    raw = base64.urlsafe_b64encode(canonical(payload)).decode().rstrip('=')
    return {**snapshot, 'confirmation_token': raw + '.' + signature(payload, 'pcm-development-snapshot'),
            'expires_at': payload['expires_at']}


def verify_snapshot(token, user_id):
    try:
        if not isinstance(token, str) or len(token) > 2048: raise ValueError()
        encoded, mac = token.split('.')
        payload = json.loads(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)))
        expected = signature(payload, 'pcm-development-snapshot')
        if not hmac.compare_digest(expected, mac): raise ValueError()
        if payload['account'] != int(user_id) or payload['expires_at'] < time.time(): raise ValueError()
        if not re.fullmatch('[a-f0-9]{64}', payload['sha256']): raise ValueError()
        return payload['sha256']
    except (ValueError, TypeError, KeyError, UnicodeError, json.JSONDecodeError):
        raise ValueError('Refresh and confirm the current master-data snapshot') from None


def account_folder(root, user_id):
    # user_id comes exclusively from the authenticated server-side User record.
    return Path(root) / str(int(user_id))


def run_folder(root, user_id, run_id):
    return account_folder(root, user_id) / str(UUID(str(run_id)))


def _input_labels(payload, key):
    """Display labels only, read from existing owner inputs; never calculation keys."""
    values = []
    for row in payload.get('rows', []):
        value = row.get('inputs', {}).get(key)
        if value not in (None, '') and str(value).strip() not in values:
            values.append(str(value).strip()[:255])
        if len(values) == 5: break
    return ', '.join(values)


def history_item(item):
    """Add presentation defaults after signature verification, including old artifacts."""
    result = dict(item)
    result.setdefault('uploaded_by', 'Account '+str(item['account']))
    result.setdefault('project', '')
    result.setdefault('part_name', '')
    result.setdefault('counts', {'total': item.get('successful_records', 0)+item.get('blocked_records', 0),
        'calculated': item.get('successful_records', 0), 'partial': 0,
        'blocked': item.get('blocked_records', 0), 'error': 0, 'unverified': 0})
    result.setdefault('status', 'partial' if item.get('blocked_records') else 'calculated')
    return result


def output_filename(payload, user_id, input_name='Calculation.xlsx'):
    """PCM5 443-460: first BOM identity + report time + authenticated actor.

    Keep identifiers intact: legacy replaces every lower-case x, which corrupts
    real project labels. Filesystem/HTTP safety replaces only unsafe characters.
    Artifact folder UUID supplies collision isolation, never workbook identity.
    """
    from datetime import timedelta
    metadata=payload.get('metadata',{})
    stamp=metadata.get('report_generated_at') or metadata.get('created_at')
    if not stamp:raise ValueError('A recorded report-generation timestamp is required')
    generated=datetime.fromisoformat(str(stamp).replace('Z','+00:00'))
    if generated.tzinfo is None:raise ValueError('Report timestamp must include timezone')
    generated=generated.astimezone(timezone(timedelta(hours=5,minutes=30)))
    rows=payload.get('rows',[]);inputs=rows[0].get('inputs',{}) if rows else {}
    parts=[str(inputs.get(key) or '').strip() for key in ('PF Code','Project','Part Name','BC / SBC / TKO / RoCM')]
    if not any(parts):parts=[Path(str(input_name).replace('\\','/')).stem]
    raw=' '.join(part for part in parts if part)+' '+generated.strftime('%d %b %Y %I.%M.%S %p')+'_'+str(int(user_id))
    safe=re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', raw).strip(' .')
    return safe[:220]+'.xlsx'


def manifest(folder, payload, output, user_id, filename, *, uploaded_by=None):
    created = datetime.now(timezone.utc).isoformat()
    metadata = payload['metadata']
    metadata.setdefault('report_generated_at', created)
    metadata['output_name'] = output_filename(payload,user_id,filename)
    result = canonical(payload)
    item = {'run_id': metadata['run_id'], 'account': int(user_id), 'created_at': created,
            'output_name': metadata['output_name'],
            'input_name': str(filename or 'input.xlsx').replace('\\', '/').split('/')[-1][:255],
            'uploaded_by': str(uploaded_by or metadata.get('uploaded_by') or 'Account '+str(int(user_id)))[:255],
            'project': _input_labels(payload, 'Project') or _input_labels(payload, 'PF Code'),
            'part_name': _input_labels(payload, 'Part Name') or _input_labels(payload, 'End Item'),
            'successful_records': metadata['successful_records'], 'blocked_records': metadata['blocked_records'],
            'counts': payload.get('counts', {'total': metadata['successful_records'] + metadata['blocked_records'],
                'calculated': metadata['successful_records'], 'partial': 0, 'blocked': metadata['blocked_records'],
                'unverified': 0, 'error': 0}),
            'status': payload.get('status', 'partial' if metadata['blocked_records'] else 'calculated'),
            'snapshot': metadata['master_snapshot'], 'verification_status': 'development-unverified',
            'production_enabled': False, 'legacy_numerically_verified': False,
            'result_sha256': sha256(result).hexdigest(), 'workbook_sha256': sha256(output).hexdigest()}
    item['integrity_mac'] = signature(item, 'pcm-development-artifact')
    (folder / 'result.json').write_bytes(result)
    (folder / 'output.xlsx').write_bytes(output)
    (folder / 'manifest.json').write_bytes(canonical(item))
    return item


def read_manifest(folder, user_id):
    try:
        item = json.loads((folder / 'manifest.json').read_bytes())
        mac = item.pop('integrity_mac')
        if item['account'] != int(user_id) or item['run_id'] != folder.name:
            raise ValueError()
        if not hmac.compare_digest(signature(item, 'pcm-development-artifact'), mac): raise ValueError()
        item['integrity_mac'] = mac
        return item
    except (OSError, ValueError, KeyError, TypeError):
        raise ValueError('Calculation output is unavailable or failed integrity verification') from None


def read_artifact(folder, user_id, name):
    item = read_manifest(folder, user_id)
    if name not in {'result.json', 'output.xlsx'}: raise ValueError('Unknown calculation artifact')
    data = (folder / name).read_bytes()
    key = 'result_sha256' if name == 'result.json' else 'workbook_sha256'
    if not hmac.compare_digest(sha256(data).hexdigest(), item[key]):
        raise ValueError('Calculation output failed integrity verification')
    return data


def preview(data, sheet=None, start=1, rows=50, column=1, columns=30):
    from openpyxl import load_workbook
    workbook = load_workbook(BytesIO(data), read_only=True, data_only=False, keep_links=False)
    try:
        sheets = [{'name': s.title, 'rows': s.max_row, 'columns': s.max_column} for s in workbook.worksheets]
        target = workbook[sheet] if sheet is not None and sheet in workbook.sheetnames else workbook.worksheets[0] if sheet is None else None
        if target is None: raise ValueError('Output worksheet was not found')
        cells = [[serialize(cell.value) if isinstance(cell.value, (Decimal, date, datetime)) else cell.value
                  for cell in row] for row in target.iter_rows(min_row=start, max_row=min(start+rows-1, target.max_row),
                                                               min_col=column, max_col=min(column+columns-1, target.max_column))]
        return {'sheets': sheets, 'sheet': target.title, 'start': start, 'column': column,
                'rows': cells, 'formulas_evaluated': False, 'production_enabled': False}
    finally:
        workbook.close()
