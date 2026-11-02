"""Original FTG parity plus isolated authenticated artifact and audit checks.

No real database users, grants, master records or sessions are modified.
"""
from io import BytesIO
from pathlib import Path
from hashlib import sha256
import importlib.util
import json
import os
import subprocess
import sys
import pytest
import openpyxl
from test_masters import isolated, login
from app.api import converters
from app.converters import storage
from app.models import User, Permission, RolePermission, AuditEvent


REFERENCE = Path('C:/Users/Admin/Downloads/CostOptima_PCM_updated (1)/CostOptima/costoptima_backend/app')


def sample_pdf(part='Fixture Housing', image=False, image_color='steelblue'):
    # Tiny deterministic real PDF, parsed by both original and migrated PyPDF2/pdfplumber.
    lines = [f'Project Name: Fixture', f'Part Name: {part}', 'Date: 27/02/2026',
        'Injection Moulding M/C 350 T', 'Part Material: ABS', 'No of Cavity: 2',
        'Part size: 200 x 150 x 30', 'Mould size: 500 x 450 x 400',
        'Mould weight: 500 kg', 'No of Lifters: 2', 'No of Sliders: 4 Nos Hydraulic',
        'Core Block - 200 x 100 x 100', 'Cavity Block - 250 x 150 x 100',
        'Mould image']
    commands = 'BT /F1 12 Tf 40 800 Td ' + ' '.join(
        ('0 -20 Td ' if index else '') + '(' + line.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)') + ') Tj'
        for index, line in enumerate(lines)) + ' ET'
    if image:
        commands += ' q 200 0 0 160 60 200 cm /Im1 Do Q'
    content = commands.encode('ascii')
    resources = '/Font << /F1 5 0 R >>' + (' /XObject << /Im1 6 0 R >>' if image else '')
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 842] /Resources << {resources} >> /Contents 4 0 R >>'.encode(),
        b'<< /Length ' + str(len(content)).encode() + b' >>\nstream\n' + content + b'\nendstream',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    if image:
        from PIL import Image
        image_bytes = BytesIO(); Image.new('RGB', (200, 160), image_color).save(image_bytes, 'JPEG')
        payload = image_bytes.getvalue()
        objects.append(b'<< /Type /XObject /Subtype /Image /Width 200 /Height 160 /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length ' + str(len(payload)).encode() + b' >>\nstream\n' + payload + b'\nendstream')
    out = bytearray(b'%PDF-1.4\n'); offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(out)); out.extend(f'{number} 0 obj\n'.encode() + obj + b'\nendobj\n')
    xref = len(out); out.extend(f'xref\n0 {len(objects)+1}\n0000000000 65535 f \n'.encode())
    for offset in offsets[1:]: out.extend(f'{offset:010d} 00000 n \n'.encode())
    out.extend(f'trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF'.encode())
    return bytes(out)


def permit(factory):
    with factory() as db:
        user = db.query(User).first()
        permission = Permission(resource='converters', action='view'); db.add(permission); db.flush()
        db.add(RolePermission(role_id=user.role_id, permission_id=permission.id)); db.commit()


def post(client, headers, documents):
    return client.post('/api/converters/ftg/process', headers=headers,
        files=[('pdf_files', (name, data, 'application/pdf')) for name, data in documents])


def workbook_contract(data):
    w = openpyxl.load_workbook(BytesIO(data), data_only=False)
    try:
        return [(s.title, s.sheet_state, s.max_row, s.max_column,
            [(c.coordinate, c.value, c.data_type, c.number_format, c.style_id,
              c.alignment.horizontal, c.alignment.vertical) for row in s for c in row if c.value is not None],
            sorted(str(x) for x in s.merged_cells.ranges),
            [(d.sqref.__str__(), d.type, d.formula1) for d in s.data_validations.dataValidation],
            [(key, d.width) for key, d in s.column_dimensions.items()],
            len(s._images)) for s in w]
    finally: w.close()


def reference_output(tmp_path, data):
    if not (REFERENCE / 'services/pdf_converter/pdf_to_json.py').is_file():
        pytest.skip('Original reference source is not available on this machine')
    location = tmp_path / 'original'; location.mkdir()
    pdf = location / 'fixture.pdf'; pdf.write_bytes(data)
    jsons = location / 'jsons'; images = location / 'images'; out = location / 'original.xlsx'
    environment = {**os.environ, 'PYTHONIOENCODING': 'utf-8', 'PYTHONDONTWRITEBYTECODE': '1'}
    for args in [[str(REFERENCE / 'services/pdf_converter/pdf_to_json.py'), '--input', str(pdf), '--out', str(jsons), '--img', str(images)],
                 [str(REFERENCE / 'services/pdf_converter/json_to_excel.py'), '--template', str(REFERENCE / 'templates/pdf_converter/Template.xlsx'), '--json-dir', str(jsons), '--out', str(out), '--cache-dir', str(images)]]:
        result = subprocess.run([sys.executable, '-B', *args], cwd=location, env=environment,
            capture_output=True, timeout=45)
        assert result.returncode == 0, 'Original converter did not complete the isolated reference fixture'
    return out.read_bytes()


def direct_conversion(tmp_path, documents):
    folder = tmp_path / 'migrated'; folder.mkdir()
    files = []
    for i, (name, data) in enumerate(documents):
        stored = f'{i:03d}.pdf'; (folder / stored).write_bytes(data)
        files.append({'name': name, 'stored_name': stored})
    (folder / 'request.json').write_text(json.dumps({'files': files}), encoding='utf-8')
    return converters.converted(folder)


@pytest.mark.parametrize('with_image', [False, True])
def test_original_and_migrated_output_contract_match(tmp_path, with_image):
    data = sample_pdf(image=with_image)
    reference = reference_output(tmp_path, data)
    result, output = direct_conversion(tmp_path, [('fixture.pdf', data)])
    assert result['status'] == 'completed'
    assert workbook_contract(output) == workbook_contract(reference)
    w = openpyxl.load_workbook(BytesIO(output)); assert 'DROP_DOWN_TAB' in w.sheetnames
    detail = next(s for s in w if s.title != 'DROP_DOWN_TAB')
    assert detail['C10'].value == 350 and detail['C2'].value == 'Fixture'
    if with_image: assert len(detail._images) == 1
    assert len(detail.data_validations.dataValidation) > 0


def test_auth_permission_csrf_and_original_capability_tabs(isolated, monkeypatch, tmp_path):
    _, factory, client = isolated; monkeypatch.setattr(converters, 'ROOT', tmp_path / 'runs')
    assert client.get('/api/converters/ftg/capabilities').status_code == 401
    headers = login(client)
    assert client.get('/api/converters/ftg/capabilities').status_code == 403
    permit(factory)
    tabs = client.get('/api/converters/ftg/capabilities').json()['tabs']
    assert [item['available'] for item in tabs] == [True, False, False]
    assert [item['label'] for item in tabs] == ['PDF to Excel Converter', 'FTG Fixture - Input to RPA', 'FTG Fixture - PPT to Input Excel']
    missing = {k: v for k, v in headers.items() if k != 'X-CSRF-Token'}
    assert post(client, missing, [('fixture.pdf', sample_pdf())]).status_code == 403


def test_real_pdf_api_history_preview_download_and_audit(isolated, monkeypatch, tmp_path):
    _, factory, client = isolated; monkeypatch.setattr(converters, 'ROOT', tmp_path / 'runs')
    headers = login(client); permit(factory)
    response = post(client, headers, [('fixture.pdf', sample_pdf(image=True))])
    assert response.status_code == 200, response.text
    item = response.json(); assert item['version'] == 1 and item['status'] == 'completed'
    assert item['converted_files'] == ['fixture.pdf'] and item['failed_files'] == []
    assert not {'account', 'integrity_mac', 'workbook_sha256'} & item.keys()
    history = client.get('/api/converters/ftg/jobs').json()
    assert history['total'] == 1 and history['items'][0]['id'] == item['id']
    assert client.get('/api/converters/ftg/jobs?q=missing').json()['total'] == 0
    preview = client.get(item['preview_url']).json(); assert 'DROP_DOWN_TAB' in [s['name'] for s in preview['sheets']]
    sheet = next(s['name'] for s in preview['sheets'] if s['name'] != 'DROP_DOWN_TAB')
    assert client.get(item['preview_url'], params={'sheet': sheet}).status_code == 200
    assert client.get(item['preview_url'], params={'sheet': 'missing'}).status_code == 404
    assert client.get(item['preview_url'], params={'rows': 101}).status_code == 422
    output = client.get(item['download_url']); assert output.status_code == 200
    w = openpyxl.load_workbook(BytesIO(output.content)); assert w[sheet]['C10'].value == 350
    with factory() as db:
        events = db.query(AuditEvent).filter_by(resource='converters').order_by(AuditEvent.id).all()
        assert {e.action for e in events} == {'conversion.started', 'conversion.completed', 'export.generated'}
        assert len({e.session_ref for e in events}) == 1
    again = post(client, headers, [('fixture.pdf', sample_pdf())])
    assert again.status_code == 200 and again.json()['version'] == 2


def test_batch_preserves_success_and_reports_failed_pdf(isolated, monkeypatch, tmp_path):
    _, factory, client = isolated; monkeypatch.setattr(converters, 'ROOT', tmp_path / 'runs')
    headers = login(client); permit(factory)
    response = post(client, headers, [('fixture.pdf', sample_pdf()), ('bad.pdf', b'%PDF-not-valid')])
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'partial'
    assert response.json()['failed_files'][0]['filename'] == 'bad.pdf'
    assert client.get(response.json()['download_url']).status_code == 200


def test_reject_invalid_path_duplicate_filetype_and_size(isolated, monkeypatch, tmp_path):
    _, factory, client = isolated; monkeypatch.setattr(converters, 'ROOT', tmp_path / 'runs')
    headers = login(client); permit(factory)
    for name, payload in [('../escape.pdf', sample_pdf()), ('document.exe', sample_pdf()), ('fake.pdf', b'plain text')]:
        assert post(client, headers, [(name, payload)]).status_code == 422
    assert post(client, headers, [('fixture.pdf', sample_pdf()), ('FIXTURE.PDF', sample_pdf())]).status_code == 422
    monkeypatch.setattr(converters, 'MAX_FILE_BYTES', 20)
    assert post(client, headers, [('large.pdf', sample_pdf())]).status_code == 413
    assert not (tmp_path / 'runs').exists()


def test_other_account_tamper_and_restart_storage(isolated, monkeypatch, tmp_path):
    from app.security import hash_password
    _, factory, client = isolated; monkeypatch.setattr(converters, 'ROOT', tmp_path / 'runs')
    headers = login(client); permit(factory)
    item = post(client, headers, [('fixture.pdf', sample_pdf())]).json()
    # Signed filesystem artifacts survive new storage reads without in-memory state.
    loaded, output = storage.load(tmp_path / 'runs', 1, item['id'], with_output=True)
    assert loaded['id'] == item['id'] and output.startswith(b'PK')
    with factory() as db:
        first = db.query(User).first()
        db.add(User(name='Other', email='other.ftg@example.com', hashed_password=hash_password('isolated other password'), role_id=first.role_id, status='Active')); db.commit()
    client.post('/api/auth/logout', headers=headers)
    second = client.post('/api/auth/login', headers={k: v for k, v in headers.items() if k != 'X-CSRF-Token'}, data={'username': 'other.ftg@example.com', 'password': 'isolated other password'})
    assert second.status_code == 200
    assert client.get('/api/converters/ftg/jobs').json()['total'] == 0
    assert client.get(item['download_url']).status_code == 404
    assert client.get(item['preview_url']).status_code == 404
    folder = tmp_path / 'runs' / '1' / item['id']; (folder / 'output.xlsx').write_bytes(b'tamper')
    with pytest.raises(ValueError): storage.load(tmp_path / 'runs', 1, item['id'], with_output=True)
    assert storage.history(tmp_path / 'runs', 1) == []


def test_failed_audit_never_publishes_artifact(isolated, monkeypatch, tmp_path):
    _, factory, client = isolated; monkeypatch.setattr(converters, 'ROOT', tmp_path / 'runs')
    headers = login(client); permit(factory)
    monkeypatch.setattr(converters, 'record_independent', lambda *a, **k: False)
    assert post(client, headers, [('fixture.pdf', sample_pdf())]).status_code == 503
    assert not (tmp_path / 'runs').exists()


def test_untrusted_pdf_text_is_never_an_excel_formula():
    from app.converters.json_to_excel import safe_write_addr, safe_write
    w = openpyxl.Workbook(); s = w.active
    safe_write_addr(s, 'A1', '=WEBSERVICE("https://untrusted.invalid")')
    safe_write(s, 2, 1, '=1+2')
    assert s['A1'].data_type == 's' and s['A2'].data_type == 's'


def test_completion_audit_failure_never_publishes_artifact(isolated, monkeypatch, tmp_path):
    _, factory, client = isolated; monkeypatch.setattr(converters, 'ROOT', tmp_path / 'runs')
    headers = login(client); permit(factory)
    def fail(*args, **kwargs): raise RuntimeError('isolated audit failure')
    monkeypatch.setattr(converters, 'record', fail)
    response = post(client, headers, [('fixture.pdf', sample_pdf())])
    assert response.status_code == 503
    assert client.get('/api/converters/ftg/jobs').json()['total'] == 0
    assert list((tmp_path / 'runs' / '1').iterdir()) == []


def test_converter_timeout_is_safe_and_creates_no_artifact(isolated, monkeypatch, tmp_path):
    _, factory, client = isolated; monkeypatch.setattr(converters, 'ROOT', tmp_path / 'runs')
    headers = login(client); permit(factory)
    def timeout(*args, **kwargs): raise subprocess.TimeoutExpired('isolated converter', 240)
    monkeypatch.setattr(converters, 'converted', timeout)
    response = post(client, headers, [('fixture.pdf', sample_pdf())])
    assert response.status_code == 504 and '240' not in response.text
    assert not (tmp_path / 'runs').exists()
    with factory() as db:
        event = db.query(AuditEvent).filter_by(resource='converters', action='conversion.failed').one()
        assert event.outcome == 'failure' and event.error_category == 'timeout'


def test_same_part_multiple_pdfs_keep_distinct_images_and_unique_sheets(tmp_path):
    result, output = direct_conversion(tmp_path, [
        ('first.pdf', sample_pdf(image=True, image_color='steelblue')),
        ('second.pdf', sample_pdf(image=True, image_color='firebrick'))])
    assert result['converted_files'] == ['first.pdf', 'second.pdf']
    w = openpyxl.load_workbook(BytesIO(output))
    details = [s for s in w if s.title != 'DROP_DOWN_TAB']
    assert len(details) == 2 and details[0].title != details[1].title
    assert all(len(s._images) == 1 for s in details)
    assert sha256(details[0]._images[0]._data()).hexdigest() != sha256(details[1]._images[0]._data()).hexdigest()
