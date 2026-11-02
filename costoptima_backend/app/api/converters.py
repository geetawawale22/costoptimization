"""Original FTG PDF conversion, integrated with existing permissions, CSRF and audit."""
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import BoundedSemaphore, Lock
from uuid import UUID, uuid4
import json
import os
import subprocess
import sys

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool
from app.database import get_db
from app.security import require_permission
from app.audit import record, record_independent
from app.converters import storage
from app.calculations.development import preview as preview_workbook

router = APIRouter(prefix='/api/converters/ftg', tags=['FTG Converter'])
VIEW = require_permission('converters', 'view')  # Exact original converter permission.
ROOT = Path(__file__).resolve().parents[2] / 'exports' / 'ftg-converter'
MAX_FILES = 16
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
SLOTS = BoundedSemaphore(2)
PUBLISH = Lock()


def converted(folder):
    environment = {**os.environ, 'PYTHONIOENCODING': 'utf-8', 'PYTHONDONTWRITEBYTECODE': '1'}
    subprocess.run([sys.executable, '-B', '-m', 'app.converters.worker', str(folder)],
        cwd=Path(__file__).resolve().parents[2], env=environment, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=240, check=True,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    result = json.loads((Path(folder) / 'result.json').read_text(encoding='utf-8'))
    output = Path(folder) / 'output.xlsx'
    if not result['converted_files']:
        raise HTTPException(422, {'message': 'None of the PDFs could be converted.', 'files': result['failed_files']})
    if not 0 < output.stat().st_size <= storage.MAX_OUTPUT_BYTES:
        raise RuntimeError('Conversion output size is invalid')
    return result, output.read_bytes()


@router.get('/capabilities')
def capabilities(user=Depends(VIEW)):
    return {'tabs': [
        {'id': 'pdf', 'label': 'PDF to Excel Converter', 'available': True},
        {'id': 'input-rpa', 'label': 'FTG Fixture - Input to RPA', 'available': False,
         'reason': 'The original application contains only an unconfigured screen for this operation. Its conversion source and template are required.'},
        {'id': 'ppt-input', 'label': 'FTG Fixture - PPT to Input Excel', 'available': False,
         'reason': 'The original application contains only an unconfigured screen for this operation. Its conversion source and template are required.'}],
        'max_files': MAX_FILES, 'max_file_bytes': MAX_FILE_BYTES, 'max_total_bytes': MAX_TOTAL_BYTES,
        'max_pages_per_file': 40, 'output_filename': 'Compiled_MSS_Report.xlsx'}


@router.get('/jobs')
def jobs(page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100),
         q: str = Query('', max_length=200), user=Depends(VIEW)):
    try:
        items = storage.history(ROOT, user.id)
    except (OSError, ValueError):
        raise HTTPException(503, 'Converter history could not be read.') from None
    if q: items = [item for item in items if q.casefold() in item['source_name'].casefold()]
    return {'items': items[(page-1)*page_size:page*page_size], 'total': len(items),
            'page': page, 'page_size': page_size}


@router.post('/process')
async def process(pdf_files: list[UploadFile] = File(...), db=Depends(get_db), user=Depends(VIEW)):
    if not 0 < len(pdf_files) <= MAX_FILES:
        raise HTTPException(422, f'Select between 1 and {MAX_FILES} PDF files.')
    if not SLOTS.acquire(blocking=False):
        raise HTTPException(429, 'The converter is busy. Try again shortly.', headers={'Retry-After': '5'})
    try:
        documents = []; total = 0; seen = set()
        # No upload path or document body is logged or trusted as a server path.
        with TemporaryDirectory(prefix='cost-pilot-ftg-') as temporary:
            folder = Path(temporary)
            for index, upload in enumerate(pdf_files):
                name = upload.filename or ''
                if (not name.lower().endswith('.pdf') or len(name) > 200 or
                    any(ch in name for ch in '/\\:') or any(ord(ch) < 32 for ch in name)):
                    raise HTTPException(422, 'Use PDF filenames without path characters.')
                if name.casefold() in seen:
                    raise HTTPException(422, 'Each uploaded PDF must have a distinct filename.')
                seen.add(name.casefold())
                data = await upload.read(MAX_FILE_BYTES + 1)
                total += len(data)
                if len(data) > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
                    raise HTTPException(413, 'PDF upload exceeds the 20 MiB per-file or 64 MiB batch limit.')
                if not data.startswith(b'%PDF-'):
                    raise HTTPException(422, 'One of the selected files is not a PDF document.')
                stored = f'{index:03d}.pdf'
                (folder / stored).write_bytes(data)
                documents.append({'name': name, 'stored_name': stored})
            (folder / 'request.json').write_text(json.dumps({'files': documents}), encoding='utf-8')
            if not record_independent('converters', 'conversion.started',
                    metadata={'converter': 'pdf_converter', 'file_count': len(documents), 'bytes': total}):
                raise HTTPException(503, 'Conversion audit storage is unavailable. Conversion was not started.')
            result, output = await run_in_threadpool(converted, folder)
            owner = storage.account(ROOT, user.id); owner.mkdir(parents=True, exist_ok=True)
            job_id = str(uuid4()); destination = storage.folder_for(ROOT, user.id, job_id)
            with PUBLISH:
                prior = [item for item in storage.history(ROOT, user.id)
                         if item['input_names'] == sorted(item['name'] for item in documents)]
                version = max((item['version'] for item in prior), default=0) + 1
                with TemporaryDirectory(prefix='.pending-', dir=owner) as pending:
                    item = storage.write_manifest(Path(pending), output, job_id=job_id, user_id=user.id,
                        names=sorted(item['name'] for item in documents), result=result, version=version)
                    record(db, 'converters', 'conversion.completed', target_type='converter_artifact', target_id=job_id,
                        metadata={'converter': 'pdf_converter', 'status': result['status'],
                                  'converted': len(result['converted_files']), 'failed': len(result['failed_files'])})
                    db.commit()
                    os.replace(pending, destination)
            return {**item, 'download_url': f'/api/converters/ftg/jobs/{job_id}/download',
                    'preview_url': f'/api/converters/ftg/jobs/{job_id}/preview'}
    except HTTPException as exc:
        db.rollback()
        if exc.status_code not in {413, 429}:
            record_independent('converters', 'conversion.failed', outcome='failure', error_category='validation')
        raise
    except subprocess.TimeoutExpired:
        db.rollback()
        record_independent('converters', 'conversion.failed', outcome='failure', error_category='timeout')
        raise HTTPException(504, 'Conversion took too long. Try a smaller batch.') from None
    except Exception:
        db.rollback()
        record_independent('converters', 'conversion.failed', outcome='failure', error_category='processing')
        raise HTTPException(503, 'The converter could not safely generate or publish the workbook.') from None
    finally:
        SLOTS.release()


def artifact(job_id, user):
    try:
        return storage.load(ROOT, user.id, job_id, with_output=True)
    except (OSError, ValueError, TypeError, KeyError):
        raise HTTPException(404, 'Converter output was not found or could not be verified.') from None


@router.get('/jobs/{job_id}/download')
def download(job_id: UUID, db=Depends(get_db), user=Depends(VIEW)):
    item, output = artifact(job_id, user)
    try:
        record(db, 'converters', 'export.generated', target_type='converter_artifact', target_id=str(job_id))
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(503, 'The download audit could not be saved.') from None
    return Response(output, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition': 'attachment; filename="Compiled_MSS_Report.xlsx"', 'Cache-Control': 'no-store'})


@router.get('/jobs/{job_id}/preview')
def preview(job_id: UUID, sheet: str | None = Query(None, max_length=31),
            start: int = Query(1, ge=1, le=1_048_576), rows: int = Query(30, ge=1, le=100),
            columns: int = Query(16, ge=1, le=100), user=Depends(VIEW)):
    _, output = artifact(job_id, user)
    try:
        return preview_workbook(output, sheet, start, rows, 1, columns)
    except ValueError:
        raise HTTPException(404, 'The selected worksheet is not available.') from None
