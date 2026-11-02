"""Bounded child process for the copied original PDF and MSS workbook logic.

Only the server invokes this module with its private temporary job directory.
No credentials, uploaded document text or parser tracebacks reach response bodies.
"""
from pathlib import Path
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
import gc
import json
import sys
import warnings

MAX_PAGES = 40
MAX_TEXT = 4_000_000


def convert(folder):
    from PIL import Image
    from PyPDF2 import PdfReader
    import pdfplumber
    from app.converters import pdf_to_json, json_to_excel
    Image.MAX_IMAGE_PIXELS = 25_000_000
    warnings.simplefilter('error', Image.DecompressionBombWarning)
    folder = Path(folder).resolve()
    request = json.loads((folder / 'request.json').read_text(encoding='utf-8'))
    documents = request['files']
    json_dir = folder / 'jsons'; json_dir.mkdir()
    image_dir = folder / 'images'; image_dir.mkdir()
    success = []; failed = []
    for item in documents:
        # Storage names come exclusively from the server, never a client path.
        path = folder / item['stored_name']
        try:
            if path.parent != folder or path.is_symlink():
                raise ValueError('invalid_document')
            reader = PdfReader(str(path), strict=False)
            if reader.is_encrypted:
                failed.append({'filename': item['name'], 'reason': 'Encrypted PDFs are not supported.'})
                continue
            if not 0 < len(reader.pages) <= MAX_PAGES:
                failed.append({'filename': item['name'], 'reason': 'PDF must contain between 1 and 40 pages.'})
                continue
            text_parts = []
            length = 0
            for page in reader.pages:
                try:
                    part = page.extract_text() or ''
                except Exception:
                    part = ''  # Original per-page fallback.
                length += len(part)
                if length > MAX_TEXT:
                    raise ValueError('document_text_limit')
                if part:
                    text_parts.append(part + '\n')
            text = ''.join(text_parts)
            if not text.strip():
                failed.append({'filename': item['name'], 'reason': 'No readable text was found. Scanned PDFs need text recognition before conversion.'})
                continue
            document_images = image_dir / path.stem
            options = {'enabled': True, 'folder': str(document_images), 'resolution': 150}
            # Preserve original build_json and its no-image fallback.
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                try:
                    with pdfplumber.open(str(path)) as pdf:
                        payload = pdf_to_json.build_json(text, pdf.pages, image_opts=options,
                            pdf_path_name=Path(item['name']).name.split('.')[0], debug=False)
                except Exception:
                    payload = pdf_to_json.build_json(text, [], image_opts={'enabled': False, 'folder': str(document_images)},
                            pdf_path_name=Path(item['name']).name.split('.')[0], debug=False)
            (json_dir / (path.stem + '.json')).write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
            success.append(item['name'])
        except Exception:
            failed.append({'filename': item['name'], 'reason': 'This PDF could not be safely parsed.'})
        gc.collect()
    if success:
        previous = sys.argv
        try:
            sys.argv = ['json_to_excel', '--template', str(Path(__file__).parent / 'templates/Template.xlsx'),
                        '--json-dir', str(json_dir), '--out', str(folder / 'output.xlsx'),
                        '--cache-dir', str(image_dir)]
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                json_to_excel.main()
        finally:
            sys.argv = previous
    result = {'converted_files': success, 'failed_files': failed,
              'status': 'partial' if failed and success else ('completed' if success else 'failed')}
    (folder / 'result.json').write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
    return result


if __name__ == '__main__':
    try:
        convert(sys.argv[1])
    except Exception:
        # The parent reports a safe generic error. Do not emit document data.
        sys.exit(1)
