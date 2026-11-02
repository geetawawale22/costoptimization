"""Snapshot identity, read-only enforcement and artifact integrity in isolated fixtures."""
from pathlib import Path
from uuid import uuid4
import json
import time
import pytest
from sqlalchemy import text
from test_masters import isolated
from app.calculations import development as dev


def test_read_only_connection_blocks_writes_and_restores_connection(isolated):
    engine, factory, client = isolated
    with dev.read_only_masters() as db:
        assert db.execute(text('SELECT count(*) FROM master_source')).scalar() == 2
        with pytest.raises(RuntimeError, match='read-only'):
            db.execute(text('UPDATE master_source SET row_count=900'))
    with engine.connect() as connection:
        assert connection.exec_driver_sql('PRAGMA query_only').scalar() == 0
        assert connection.execute(text('SELECT sum(row_count) FROM master_source')).scalar() == 0


def test_snapshot_covers_sources_and_metadata_and_is_stable(isolated):
    engine, factory, client = isolated
    with dev.read_only_masters() as db:
        first = dev.master_snapshot(db)
    with dev.read_only_masters() as db:
        assert dev.master_snapshot(db)['sha256'] == first['sha256']
    assert first['master_count'] == 1 and first['source_count'] == 2
    assert first['source_rows'] == 0
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO src_fixture__master(row_id,code,rate) VALUES (1,'0007',1.25)"))
    with dev.read_only_masters() as db:
        changed = dev.master_snapshot(db)
    assert changed['sha256'] != first['sha256'] and changed['source_rows'] == 1
    with engine.begin() as connection:
        connection.execute(text("UPDATE master_column SET header_text='Changed' WHERE source_id=1 AND column_key='rate'"))
    with dev.read_only_masters() as db:
        assert dev.master_snapshot(db)['sha256'] != changed['sha256']


def test_snapshot_confirmation_is_account_bound_tamper_proof_and_expiring(monkeypatch):
    proof = dev.issue_snapshot(7, {'sha256': 'a'*64})
    assert dev.verify_snapshot(proof['confirmation_token'], 7) == 'a'*64
    for token, account in [(proof['confirmation_token'], 8), (proof['confirmation_token'][:-1]+'x', 7), ('forged', 7)]:
        with pytest.raises(ValueError, match='Refresh'):
            dev.verify_snapshot(token, account)
    monkeypatch.setattr(dev.time, 'time', lambda: proof['expires_at']+1)
    with pytest.raises(ValueError, match='Refresh'):
        dev.verify_snapshot(proof['confirmation_token'], 7)


def test_artifact_manifest_prevents_cross_account_reads_and_tampering(tmp_path):
    rid = str(uuid4()); folder = tmp_path / rid; folder.mkdir()
    payload = {'metadata': {'run_id': rid, 'successful_records': 1, 'blocked_records': 0,
                           'master_snapshot': {'sha256': 'a'*64}}}
    item = dev.manifest(folder, payload, b'xlsx-fixture', 7, r'C:\uploads\safe.xlsx')
    assert item['input_name'] == 'safe.xlsx'
    assert dev.read_artifact(folder, 7, 'output.xlsx') == b'xlsx-fixture'
    with pytest.raises(ValueError): dev.read_artifact(folder, 8, 'output.xlsx')
    (folder / 'output.xlsx').write_bytes(b'changed')
    with pytest.raises(ValueError, match='integrity'): dev.read_artifact(folder, 7, 'output.xlsx')
    manifest = json.loads((folder / 'manifest.json').read_bytes()); manifest['account'] = 8
    (folder / 'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError): dev.read_manifest(folder, 8)


def test_preview_is_bounded_and_never_evaluates_formula():
    from openpyxl import Workbook
    from io import BytesIO
    w = Workbook(); w.active.title = 'Details'; w.active.append(['Code', 'Formula']); w.active.append(['0007', '=1+2'])
    w.create_sheet('Diagnostics').append(['Development only'])
    buffer = BytesIO(); w.save(buffer)
    result = dev.preview(buffer.getvalue(), sheet='Details', start=2, rows=1, column=1, columns=2)
    assert result['rows'] == [['0007', '=1+2']]
    assert result['formulas_evaluated'] is False
    assert [sheet['name'] for sheet in result['sheets']] == ['Details', 'Diagnostics']
    with pytest.raises(ValueError): dev.preview(buffer.getvalue(), sheet='Missing')


def test_partial_counts_are_integrity_protected_in_history_manifest(tmp_path):
    rid=str(uuid4());folder=tmp_path/rid;folder.mkdir()
    counts={'total':5,'calculated':1,'partial':1,'blocked':1,'unverified':1,'error':1}
    payload={'metadata':{'run_id':rid,'successful_records':1,'blocked_records':4,
                         'master_snapshot':{'sha256':'a'*64}},'counts':counts,'status':'partial'}
    item=dev.manifest(folder,payload,b'isolated-artifact',7,'partial.xlsx')
    assert item['counts']==counts and item['status']=='partial'
    loaded=dev.read_manifest(folder,7)
    assert loaded['counts']==counts
    loaded['counts']['partial']=0
    (folder/'manifest.json').write_text(json.dumps(loaded),encoding='utf-8')
    with pytest.raises(ValueError):dev.read_manifest(folder,7)
