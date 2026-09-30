import gc
import io
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path
from typing import List

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.core.deps import require_permission
from app.database import get_db
from app.models.converter import ConverterJob
from app.models.mixins import stamp_create

logger = logging.getLogger("costoptima.pdf_converter")

router = APIRouter(prefix="/pdf-converter", tags=["PDF Converter"])

APP_ROOT = Path(__file__).resolve().parents[1]
CONVERTER_ROOT = APP_ROOT / "services" / "pdf_converter"
PDF_TO_JSON_SCRIPT = CONVERTER_ROOT / "pdf_to_json.py"
JSON_TO_EXCEL_SCRIPT = CONVERTER_ROOT / "json_to_excel.py"
TEMPLATE_FILE = APP_ROOT / "templates" / "pdf_converter" / "Template.xlsx"
OUTPUT_FILENAME = "Compiled_MSS_Report.xlsx"
PDF_CONVERTER_TYPE = "pdf_converter"
FTG_FLOW_TYPE = "ftg"


def _validate_converter_files() -> None:
    missing = [
        str(path)
        for path in (PDF_TO_JSON_SCRIPT, JSON_TO_EXCEL_SCRIPT, TEMPLATE_FILE)
        if not path.is_file()
    ]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"PDF converter files are missing: {', '.join(missing)}",
        )


def _source_name(filenames: List[str]) -> str:
    source = ", ".join(sorted(name for name in filenames if name))
    return source[:500]


def _next_version(db: Session, converter_type: str, source_name: str) -> int:
    latest = (
        db.query(ConverterJob)
        .filter(
            ConverterJob.converter_type == converter_type,
            ConverterJob.source_name == source_name,
            ConverterJob.is_deleted.is_(False),
        )
        .order_by(ConverterJob.version.desc())
        .first()
    )
    return int(latest.version or 0) + 1 if latest else 1


def _save_converter_output(db: Session, user, converter_type: str, source_name: str, source_file: Path) -> ConverterJob:
    version = _next_version(db, converter_type, source_name)
    output_dir = Path(settings.UPLOAD_DIR) / "converter_outputs" / converter_type
    output_dir.mkdir(parents=True, exist_ok=True)
    output_filename = f"{converter_type}_v{version}_{source_file.name}"
    output_path = output_dir / output_filename
    shutil.copyfile(source_file, output_path)

    job = ConverterJob(
        flow_type=FTG_FLOW_TYPE,
        converter_type=converter_type,
        source_name=source_name,
        version=version,
        status="Completed",
        output_filename=output_filename,
        output_file_path=os.path.relpath(output_path, settings.UPLOAD_DIR).replace(os.sep, "/"),
    )
    stamp_create(job, user.id)
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


@router.post("/process")
def process_pdf_files(
    pdf_files: List[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
    user=Depends(require_permission("outputs", "view")),
):
    _validate_converter_files()
    if not pdf_files or not pdf_files[0].filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No files uploaded")

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            pdf_dir = job_dir / "pdfs"
            json_dir = job_dir / "jsons"
            img_dir = job_dir / "images"
            out_excel = job_dir / "Output_Combined.xlsx"

            pdf_dir.mkdir(parents=True, exist_ok=True)
            json_dir.mkdir(parents=True, exist_ok=True)
            img_dir.mkdir(parents=True, exist_ok=True)

            file_count = 0
            uploaded_filenames = []
            for upload in pdf_files:
                filename = upload.filename or ""
                if not filename.lower().endswith(".pdf"):
                    continue
                if "/" in filename or "\\" in filename or ":" in filename:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid PDF filename")

                file_path = pdf_dir / filename
                with file_path.open("wb") as destination:
                    destination.write(upload.file.read())
                file_count += 1
                uploaded_filenames.append(filename)

            if file_count == 0:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No valid PDF files uploaded")

            pdf_paths = [path for path in pdf_dir.iterdir() if path.suffix.lower() == ".pdf"]
            env = {**os.environ, "PYTHONIOENCODING": "utf-8"}

            failed_pdfs = []
            for index, pdf_path in enumerate(pdf_paths, 1):
                logger.info("Converting PDF %s/%s: %s", index, len(pdf_paths), pdf_path.name)
                result = subprocess.run(
                    [
                        sys.executable,
                        str(PDF_TO_JSON_SCRIPT),
                        "--input",
                        str(pdf_path),
                        "--out",
                        str(json_dir),
                        "--img",
                        str(img_dir),
                    ],
                    check=False,
                    timeout=120,
                    stdin=subprocess.DEVNULL,
                    cwd=str(CONVERTER_ROOT),
                    env=env,
                )
                if result.returncode != 0:
                    logger.warning("Failed to convert %s, skipping.", pdf_path.name)
                    failed_pdfs.append(pdf_path.name)
                gc.collect()

            if failed_pdfs:
                logger.warning("%s PDF(s) skipped: %s", len(failed_pdfs), failed_pdfs)

            json_count = len([path for path in json_dir.iterdir() if path.suffix == ".json"])
            if json_count == 0:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="All PDFs failed to convert. Please check logs.",
                )

            result = subprocess.run(
                [
                    sys.executable,
                    str(JSON_TO_EXCEL_SCRIPT),
                    "--template",
                    str(TEMPLATE_FILE),
                    "--json-dir",
                    str(json_dir),
                    "--out",
                    str(out_excel),
                ],
                capture_output=True,
                text=True,
                timeout=600,
                stdin=subprocess.DEVNULL,
                cwd=str(CONVERTER_ROOT),
                env=env,
            )
            if result.returncode != 0:
                logger.error("JSON to Excel failed with return code %s", result.returncode)
                logger.error("Subprocess stderr logs:\n%s", result.stderr)
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Excel generation failed. Error details:\n{result.stderr}",
                )

            excel_bytes = out_excel.read_bytes()
            source_name = _source_name(uploaded_filenames)
            job = _save_converter_output(db, user, PDF_CONVERTER_TYPE, source_name, out_excel)
            headers = {
                "Content-Disposition": f'attachment; filename="{OUTPUT_FILENAME}"',
                "X-Converter-Job-Id": str(job.id),
                "X-Converter-Version": str(job.version),
            }
            return StreamingResponse(
                io.BytesIO(excel_bytes),
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                headers=headers,
            )
    except HTTPException:
        raise
    except subprocess.TimeoutExpired:
        logger.error("PDF converter processing timed out")
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Processing took too long. Please try with fewer files or contact support.",
        )
    except Exception as exc:
        logger.error("Critical error in PDF converter: %s\n%s", exc, traceback.format_exc())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Critical error: {exc}",
        )


@router.get("/jobs")
def list_converter_jobs(
    flow_type: str | None = None,
    converter_type: str | None = None,
    db: Session = Depends(get_db),
    _user=Depends(require_permission("outputs", "view")),
):
    query = db.query(ConverterJob).filter(ConverterJob.is_deleted.is_(False))
    if flow_type:
        query = query.filter(ConverterJob.flow_type == flow_type)
    if converter_type:
        query = query.filter(ConverterJob.converter_type == converter_type)
    jobs = query.order_by(ConverterJob.created_at.desc(), ConverterJob.id.desc()).all()
    return [
        {
            "id": job.id,
            "flow_type": job.flow_type,
            "converter_type": job.converter_type,
            "source_name": job.source_name,
            "version": job.version,
            "status": job.status,
            "output_filename": job.output_filename,
            "created_at": job.created_at,
            "created_by": job.created_by,
        }
        for job in jobs
    ]


@router.get("/jobs/{job_id}/download")
def download_converter_job(
    job_id: int,
    db: Session = Depends(get_db),
    _user=Depends(require_permission("outputs", "view")),
):
    job = db.query(ConverterJob).filter(ConverterJob.id == job_id, ConverterJob.is_deleted.is_(False)).first()
    if not job:
        raise HTTPException(status_code=404, detail="Converter output not found")
    output_path = Path(settings.UPLOAD_DIR) / (job.output_file_path or "")
    if not output_path.is_file():
        raise HTTPException(status_code=409, detail="Stored converter output file is missing")
    return StreamingResponse(
        io.BytesIO(output_path.read_bytes()),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{job.output_filename}"'},
    )

