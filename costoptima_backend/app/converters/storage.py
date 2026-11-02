"""Signed append-only conversion artifacts; no new database or authentication system."""
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from uuid import UUID
import hmac
import json
from app.config import get_settings

MAX_OUTPUT_BYTES = 128 * 1024 * 1024


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')


def signature(item):
    return hmac.new(get_settings().csrf_signing_key.encode(), b'ftg-artifact:' + canonical(item), 'sha256').hexdigest()


def account(root, user_id):
    base = Path(root).resolve()
    folder = base / str(int(user_id))
    if folder.is_symlink() or folder.resolve().parent != base:
        raise ValueError('Invalid artifact location')
    return folder


def folder_for(root, user_id, job_id):
    owner = account(root, user_id)
    folder = owner / str(UUID(str(job_id)))
    if folder.is_symlink() or folder.resolve().parent != owner.resolve():
        raise ValueError('Invalid artifact location')
    return folder


def write_manifest(folder, output, *, job_id, user_id, names, result, version):
    if not output or len(output) > MAX_OUTPUT_BYTES:
        raise ValueError('Invalid converter output')
    item = {'id': str(UUID(str(job_id))), 'account': int(user_id),
            'converter_type': 'pdf_converter', 'flow_type': 'ftg',
            'source_name': ', '.join(sorted(names))[:500], 'input_names': names,
            'version': int(version), 'status': result['status'],
            'created_at': datetime.now(timezone.utc).isoformat(),
            'output_filename': 'Compiled_MSS_Report.xlsx',
            'converted_files': result['converted_files'], 'failed_files': result['failed_files'],
            'workbook_sha256': sha256(output).hexdigest(), 'workbook_bytes': len(output)}
    item['integrity_mac'] = signature(item)
    (Path(folder) / 'output.xlsx').write_bytes(output)
    (Path(folder) / 'manifest.json').write_bytes(canonical(item))
    return public(item)


def load(root, user_id, job_id, *, with_output=False):
    folder = folder_for(root, user_id, job_id)
    path = folder / 'manifest.json'
    output_path = folder / 'output.xlsx'
    if path.is_symlink() or output_path.is_symlink() or path.stat().st_size > 64 * 1024:
        raise ValueError('Invalid artifact')
    item = json.loads(path.read_text(encoding='utf-8'))
    mac = item.pop('integrity_mac', '')
    if not hmac.compare_digest(signature(item), mac):
        raise ValueError('Invalid artifact integrity')
    if item['account'] != int(user_id) or item['id'] != str(UUID(str(job_id))):
        raise ValueError('Invalid artifact owner')
    if not 0 < output_path.stat().st_size == item['workbook_bytes'] <= MAX_OUTPUT_BYTES:
        raise ValueError('Missing artifact')
    output = None
    if with_output:
        output = output_path.read_bytes()
        if not hmac.compare_digest(sha256(output).hexdigest(), item['workbook_sha256']):
            raise ValueError('Invalid artifact integrity')
    return public(item), output


def public(item):
    return {key: value for key, value in item.items() if key not in {'account', 'integrity_mac', 'workbook_sha256'}}


def history(root, user_id):
    owner = account(root, user_id)
    if not owner.is_dir(): return []
    records = []
    for folder in owner.iterdir():
        if not folder.is_dir() or folder.name.startswith('.') or folder.is_symlink(): continue
        try:
            item, _ = load(root, user_id, folder.name)
            records.append(item)
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            continue
    return sorted(records, key=lambda row: (row['created_at'], row['id']), reverse=True)
