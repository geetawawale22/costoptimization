"""Guarded development calculations; production costing remains disabled."""
from pathlib import Path
from urllib.parse import quote
from datetime import datetime, timezone
from tempfile import TemporaryDirectory
from threading import BoundedSemaphore
from uuid import UUID
import json
import os
from hashlib import sha256
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query
from fastapi.responses import Response
from app.config import get_settings
from app.database import get_db
from app.security import require_permission
from app.audit import record, record_independent
from app.calculations.engine import execute
from app.calculations.dispatcher import catalogue
from app.calculations.masters import SemanticMasterResolver
from app.calculations.input import InputValidationError, MAX_FILE_BYTES
from app.calculations.output import build_workbook
from app.calculations.common import CalculationError
from app.calculations import development as dev

router = APIRouter(prefix='/api/calculations/development', tags=['Calculations'])
VIEW = require_permission('boms', 'view')
CREATE = require_permission('boms', 'create')
ROOT = Path(__file__).resolve().parents[2] / 'exports' / 'calculation-runs'
SLOTS = BoundedSemaphore(2)


def guard():
    if not get_settings().development_calculations_available:
        raise HTTPException(409, 'Calculations are not available in this installation. Contact your administrator.')


def snapshot_for(user_id):
    if not SLOTS.acquire(blocking=False):
        raise HTTPException(429, 'Calculations are busy; try again shortly', headers={'Retry-After': '5'})
    try:
        with dev.read_only_masters() as masters_db:
            return dev.issue_snapshot(user_id, dev.master_snapshot(masters_db))
    finally:
        SLOTS.release()


def calculate(data, expected_snapshot):
    with dev.read_only_masters() as masters_db:
        snapshot = dev.master_snapshot(masters_db)
        if snapshot['sha256'] != expected_snapshot:
            raise CalculationError('stale_master_snapshot', 'Master data changed; refresh and confirm the current snapshot before running')
        result = execute(data, SemanticMasterResolver(masters_db), development=True)
        result.metadata['master_snapshot'] = snapshot
        result.metadata['input_sha256'] = sha256(data).hexdigest()
        result.metadata['verification_status'] = 'development-unverified'
        result.metadata['production_costing_enabled'] = False
    return result, build_workbook(result)


@router.get('/capabilities')
def capabilities(user=Depends(VIEW)):
    return {'development_enabled': get_settings().development_calculations_available,
            'environment_verified': get_settings().development_environment_verified,
            'explicit_confirmation_required': True, 'master_access': 'database-enforced-read-only',
            'production_enabled': False, 'legacy_numerically_verified': False, 'branches': catalogue()}


@router.get('/snapshot')
def snapshot(user=Depends(CREATE)):
    guard()
    try:
        return snapshot_for(user.id)
    except (OSError, SQLAlchemyError, RuntimeError, ValueError):
        raise HTTPException(503, 'A read-only master snapshot could not be verified; calculation was not enabled') from None


@router.post('/runs')
async def run(file: UploadFile = File(...), confirmation: str = Form(''), snapshot_token: str = Form(''),
              db=Depends(get_db), user=Depends(CREATE)):
    guard()
    if confirmation != dev.CONFIRMATION:
        raise HTTPException(409, 'Confirm the reviewed master-data snapshot before calculating.')
    try:
        expected_snapshot = dev.verify_snapshot(snapshot_token, user.id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    if not str(file.filename or '').lower().endswith(('.xlsx', '.xlsm')):
        raise HTTPException(422, 'Use an .xlsx or .xlsm workbook')
    data = await file.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise HTTPException(413, 'Workbook exceeds the 64 MiB limit')
    if not SLOTS.acquire(blocking=False):
        raise HTTPException(429, 'Calculations are busy; try again shortly', headers={'Retry-After': '5'})
    try:
        if not record_independent('calculations', 'execution.started', metadata={
                'bytes': len(data), 'input_sha256': sha256(data).hexdigest(),
                'input_name': str(file.filename or 'input.xlsx').replace('\\','/').split('/')[-1][:255],
                'snapshot_sha256': expected_snapshot, 'production_enabled': False}):
            raise HTTPException(503, 'Calculation audit storage is unavailable; execution was not started.')
        result, artifact = await run_in_threadpool(calculate, data, expected_snapshot)
        run_id = result.metadata['run_id']
        owner = dev.account_folder(ROOT, user.id)
        owner.mkdir(parents=True, exist_ok=True)
        destination = dev.run_folder(ROOT, user.id, run_id)
        if destination.exists():
            raise RuntimeError('Calculation identity collision')
        payload = result.as_dict()
        payload['metadata']['uploaded_by'] = user.name
        payload['metadata']['input_name'] = str(file.filename or 'input.xlsx').replace('\\','/').split('/')[-1][:255]
        payload['metadata']['project'] = dev._input_labels(payload, 'Project') or dev._input_labels(payload, 'PF Code')
        payload['metadata']['part_name'] = dev._input_labels(payload, 'Part Name') or dev._input_labels(payload, 'End Item')
        payload['download_url'] = f'/api/calculations/development/runs/{run_id}/workbook'
        payload['preview_url'] = f'/api/calculations/development/runs/{run_id}/preview'
        # Pending artifacts are inaccessible until completion audit commits.
        with TemporaryDirectory(prefix='.pending-', dir=owner) as temporary:
            pending = Path(temporary)
            dev.manifest(pending, payload, artifact, user.id, file.filename, uploaded_by=user.name)
            record(db, 'calculations', 'execution.completed',
                   outcome='success',
                   target_type='development_run', target_id=run_id, metadata={
                       'rows': len(result.rows), 'success': result.metadata['successful_records'],
                       'run_id': run_id, 'input_name': payload['metadata']['input_name'],
                       'project': payload['metadata']['project'],
                       'blocked': result.metadata['blocked_records'], 'calculation_status': payload['status'],
                       'counts': payload['counts'], 'artifact_generated': True,
                       'snapshot_sha256': expected_snapshot,
                       'production_enabled': False, 'legacy_numerically_verified': False})
            db.commit()
            os.replace(pending, destination)
        return json.loads(json.dumps(payload, default=dev.serialize))
    except HTTPException:
        raise
    except (InputValidationError, CalculationError) as exc:
        db.rollback()
        record_independent('calculations', 'execution.failed', outcome='failure', error_category=type(exc).__name__,
                           metadata={'input_name': str(file.filename or 'input.xlsx').replace('\\','/').split('/')[-1][:255]})
        raise HTTPException(409 if getattr(exc, 'code', '') == 'stale_master_snapshot' else 422, str(exc)) from None
    except Exception as exc:
        db.rollback()
        record_independent('calculations', 'execution.failed', outcome='failure', error_category=type(exc).__name__,
                           metadata={'input_name': str(file.filename or 'input.xlsx').replace('\\','/').split('/')[-1][:255]})
        raise HTTPException(503, 'The calculation could not be completed safely. Please try again or contact your administrator.') from None
    finally:
        SLOTS.release()


def artifact_for(run_id, user, name):
    try:
        return dev.read_artifact(dev.run_folder(ROOT, user.id, run_id), user.id, name)
    except (ValueError, OSError):
        raise HTTPException(404, 'Verified calculation output not found for this account') from None


def _utc_datetime(value):
    if value.tzinfo is None: return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@router.get('/runs')
def history(page: int = Query(1, ge=1), page_size: int = Query(10, ge=1, le=50),
            q: str = Query('', max_length=200), status: str = Query('', max_length=20),
            date_from: datetime | None = None, date_to: datetime | None = None,
            sort: str = Query('created_desc', max_length=30), user=Depends(VIEW)):
    guard()
    if status and status not in {'calculated','partial','blocked','unverified','error'}:
        raise HTTPException(422, 'Select a valid calculation status')
    sorts = {'created_desc': ('created_at', True), 'created_asc': ('created_at', False),
             'name_asc': ('input_name', False), 'name_desc': ('input_name', True),
             'status_asc': ('status', False)}
    if sort not in sorts: raise HTTPException(422, 'Select a valid calculation sort order')
    if date_from and date_to and _utc_datetime(date_from) > _utc_datetime(date_to):
        raise HTTPException(422, 'The start date must precede the end date')
    folder = dev.account_folder(ROOT, user.id)
    items = []
    if folder.is_dir():
        for path in folder.iterdir():
            if not path.is_dir(): continue
            try:
                UUID(path.name)
                item = dev.history_item(dev.read_manifest(path, user.id))
                created = _utc_datetime(datetime.fromisoformat(item['created_at']))
                if status and item['status'] != status: continue
                if date_from and created < _utc_datetime(date_from): continue
                if date_to and created > _utc_datetime(date_to): continue
                if q.strip().casefold() not in ' '.join(str(item.get(key,'')) for key in
                        ('input_name','project','part_name','uploaded_by','run_id')).casefold(): continue
                items.append(item)
            except (ValueError, OSError, KeyError, TypeError):
                continue
    key, reverse = sorts[sort]
    items.sort(key=lambda item: (str(item.get(key,'')).casefold(), item['run_id']), reverse=reverse)
    start = (page - 1) * page_size
    return {'items': items[start:start+page_size], 'total': len(items), 'page': page,
            'page_size': page_size, 'total_pages': max(1,(len(items)+page_size-1)//page_size),
            'production_enabled': False}


@router.get('/runs/{run_id}')
def result(run_id: UUID, user=Depends(VIEW)):
    guard()
    return json.loads(artifact_for(run_id, user, 'result.json'))


@router.get('/runs/{run_id}/preview')
def preview(run_id: UUID, sheet: str | None = Query(None, max_length=31),
            start: int = Query(1, ge=1, le=1_048_576), rows: int = Query(50, ge=1, le=100),
            column: int = Query(1, ge=1, le=16_384), columns: int = Query(30, ge=1, le=100), user=Depends(VIEW)):
    guard()
    try:
        return dev.preview(artifact_for(run_id, user, 'output.xlsx'), sheet, start, rows, column, columns)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from None


@router.get('/runs/{run_id}/workbook')
def workbook(run_id: UUID, db=Depends(get_db), user=Depends(VIEW)):
    guard()
    data = artifact_for(run_id, user, 'output.xlsx')
    try:
        record(db, 'calculations', 'export.generated', target_type='development_run',
               target_id=str(run_id), metadata={'production_enabled': False})
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(503, 'Calculation export audit could not be persisted') from None
    item=dev.read_manifest(dev.run_folder(ROOT,user.id,run_id),user.id)
    name=item.get('output_name','Cost Pilot Calculation Output.xlsx')
    return Response(data, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    headers={'Content-Disposition': "attachment; filename*=UTF-8''"+quote(name,safe=''),
                             'Cache-Control': 'no-store'})
