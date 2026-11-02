"""Explicit development execution, using current database masters read-only.

python -B -m app.calculations.cli input.xlsx --development --output exports/...
The flag never changes API/production activation and never writes master values.
"""
import argparse,json
from pathlib import Path
from decimal import Decimal
from datetime import date,datetime
from sqlalchemy.orm import Session
from app.database import engine
from .engine import execute
from .masters import SemanticMasterResolver
from .output import build_workbook


def serial(value):
    if isinstance(value,Decimal):return str(value)
    if isinstance(value,(date,datetime)):return value.isoformat()
    raise TypeError(type(value).__name__)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input');parser.add_argument('--development',action='store_true');parser.add_argument('--output',required=True)
    args=parser.parse_args()
    target=Path(args.output).resolve();root=Path(__file__).resolve().parents[2]
    if not target.is_relative_to(root):parser.error('Output must remain inside the new backend project')
    if not args.development:parser.error('Explicit --development is required; production is disabled')
    with engine.connect() as connection:
        connection.exec_driver_sql('SET TRANSACTION READ ONLY')
        with Session(bind=connection) as db:run=execute(Path(args.input),SemanticMasterResolver(db),development=True)
        connection.rollback()
    target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(build_workbook(run))
    summary=target.with_suffix('.json');summary.write_text(json.dumps(run.as_dict(),default=serial,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({'output':str(target),'summary':str(summary),'records':len(run.rows),'success':sum(r.status=='success' for r in run.rows),
                      'blocked':sum(r.status!='success' for r in run.rows),'legacy_numerically_verified':False,'production_enabled':False}))

if __name__=='__main__':main()
