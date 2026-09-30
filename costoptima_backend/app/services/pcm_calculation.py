"""Application service connecting the database PCM runtime to existing APIs."""
import hashlib
import os
import copy
from datetime import datetime
from pathlib import Path

from app.config import settings
from app.models.calculation import Calculation, CalculationCommodityResult, CalculationResult, CalculationLog
from app.models.pcm_run import PcmRunSnapshot
from app.models.mixins import stamp_create
from app.pcm.engine import Interpreter, read_inputs, snapshot_masters, digest, write_snapshot_output, number, field
from app.pcm.master_data import columns_for_commodities, load_master_output_matrix


def _definition_with_output_matrix(db, definition, results):
    """Snapshot DB-imported output matrix config into this run's definition."""
    matrix = load_master_output_matrix(db)
    return definition_with_output_matrix_rows(definition, results, matrix)


def definition_with_output_matrix_rows(definition, results, matrix):
    """Return a definition copy carrying matrix-derived Master_Output columns."""
    configured = copy.deepcopy(definition)
    if not matrix:
        return configured
    profiles = {row.get("profile") for row in results if row.get("profile")}
    profile_columns = configured.get("output", {}).get("profile_master_output_columns", {})
    commodities = [
        profile_columns[profile]
        for profile in sorted(profiles)
        if profile in profile_columns and profile_columns[profile]
    ]
    output = configured.setdefault("output", {})
    output["master_output_matrix_rows"] = matrix
    output["master_output_columns"] = columns_for_commodities(matrix, commodities)
    output["master_output_matrix_profiles"] = sorted(profiles)
    return configured


def _ig_candidates_for(path: str) -> list[Path]:
    source = Path(path)
    stem = source.stem
    if stem.casefold().endswith("_ig"):
        original_stem = stem[:-3]
        return [source.with_name(f"{original_stem}{source.suffix}")]
    return [
        source.with_name(f"{stem}_ig{source.suffix}"),
        source.with_name(f"{stem} _ig{source.suffix}"),
    ]


def run_calculation(db, bom, user, definition, trace=None):
    started_at = datetime.utcnow()
    if trace:
        trace.section("Starting PCM calculation", bom_id=bom.id, input_file=bom.file_path,
                      definition_revision=definition.get("revision"))
        candidates = _ig_candidates_for(bom.file_path)
        found = [str(path) for path in candidates if path.exists()]
        trace.add("_IG_CHECK", "Checking for previous-project _ig companion files",
                  original_uploaded_file=bom.file_path,
                  candidate_paths=[str(path) for path in candidates],
                  found_existing_ig_files=found,
                  used_by_costoptima=False,
                  reason=("CostOptima reads the original uploaded workbook. It does not search for or use "
                          "an existing _ig workbook. Formula cache reads use openpyxl data_only=True on the "
                          "same original file, not a separate _ig file."))
        try:
            from openpyxl import load_workbook
            workbook = load_workbook(bom.file_path, read_only=True, data_only=False)
            trace.add("INPUT_WORKBOOK", "Opened uploaded workbook",
                      path=bom.file_path, sheets=workbook.sheetnames)
            workbook.close()
        except Exception as exc:
            trace.error("Could not open uploaded workbook before calculation", reason=str(exc))
    rows = read_inputs(bom.file_path, definition)
    if trace:
        trace.add("INPUT_SUMMARY", "Calculation input rows loaded", rows=len(rows),
                  sample=rows[:2])
    if not rows:
        from app.pcm.engine import PcmError
        raise PcmError("Input BOM contains no calculation rows")
    masters = snapshot_masters(db, definition, trace=trace)
    results = Interpreter(definition, masters, trace=trace).run(rows)
    if trace:
        completed = sum(1 for row in results if row.get("status") == "Completed")
        skipped = sum(1 for row in results if row.get("status") == "Skipped")
        review = sum(1 for row in results if row.get("status") == "Needs Review")
        trace.section("Finished PCM row calculations",
                      total_rows=len(results), completed=completed,
                      skipped=skipped, needs_review=review)
    definition = _definition_with_output_matrix(db, definition, results)
    status = "Needs Review" if any(r["status"] != "Completed" for r in results) else "Completed"
    calc = Calculation(bom_id=bom.id, status=status, started_at=started_at)
    stamp_create(calc, user.id)
    db.add(calc)
    db.flush()
    with open(bom.file_path, "rb") as stream:
        input_hash = hashlib.file_digest(stream, "sha256").hexdigest()
    snapshot = PcmRunSnapshot(calculation_id=calc.id, input_sha256=input_hash,
                              definition_sha256=digest(definition), definition=definition,
                              masters=masters, results=results)
    stamp_create(snapshot, user.id)
    db.add(snapshot)
    groups = {}
    total = 0
    for row_index, row in enumerate(results, 1):
        for issue in row.get("issues", []):
            error_log = CalculationLog(calculation_id=calc.id, level="Error", process=issue["code"],
                                       message=f"Line {issue.get('line', row_index)} / {issue.get('customer_no', '')}: {issue['message']}"[:500])
            stamp_create(error_log, user.id)
            db.add(error_log)
        # Skipped operation rows explicitly carry profile=None.
        code = row.get("profile") or "unconfigured"
        group = groups.setdefault(code, {"records": 0, "total": 0, "status": "Completed"})
        group["records"] += 1
        if row["status"] != "Completed":
            group["status"] = "Needs Review"
            continue
        amount = number(row["values"]["Total"]) * number(field(row["input"], definition["output"]["quantity_field"], 1))
        group["total"] += amount
        total += amount
        if trace:
            trace.add("TOTAL_ACCUMULATE", "Added completed row to calculation total",
                      profile=code,
                      row_total=row["values"]["Total"],
                      quantity=field(row["input"], definition["output"]["quantity_field"], 1),
                      contribution=amount,
                      running_total=total)
    for code, group in groups.items():
        sub = CalculationCommodityResult(calculation_id=calc.id, commodity_code=code, status=group["status"],
                                          records_found=group["records"], total_cost=group["total"])
        stamp_create(sub, user.id)
        db.add(sub)
    output_path = Path(settings.UPLOAD_DIR) / "outputs" / f"calc_{calc.id}_pcm.xlsx"
    try:
        if trace:
            trace.section("Writing output workbook", output_path=str(output_path))
        write_snapshot_output(snapshot, output_path)
        result = CalculationResult(calculation_id=calc.id, total_cost=total,
                                   savings_opportunity=0, confidence_pct=0,
                                   breakdown={k: v["total"] for k, v in groups.items()},
                                   output_file_path=os.path.relpath(output_path, settings.UPLOAD_DIR).replace(os.sep, "/"), email_sent="No")
        stamp_create(result, user.id)
        db.add(result)
        log = CalculationLog(calculation_id=calc.id, level="Warning" if status != "Completed" else "Success",
                              process="PCM Database Runtime",
                              message=f"{len(results)} rows; {sum(r['status'] != 'Completed' for r in results)} require review. Formula/master snapshot saved; output contains calculated values. Savings/confidence not estimated.")
        stamp_create(log, user.id)
        db.add(log)
        if trace:
            trace.section("FINAL SUMMARY",
                          flow=("Original Input -> Processing -> Data Identification -> _ig usage check -> "
                                "Database master snapshot -> Formula calculations -> Legacy output workbook"),
                          original_input=bom.file_path,
                          ig_used=False,
                          calculated_rows=len(results),
                          completed_rows=sum(1 for row in results if row.get("status") == "Completed"),
                          needs_review_rows=sum(1 for row in results if row.get("status") == "Needs Review"),
                          skipped_rows=sum(1 for row in results if row.get("status") == "Skipped"),
                          total_cost=total,
                          output=str(output_path))
            for line in trace.summary_lines():
                db.add(CalculationLog(calculation_id=calc.id, level="Info",
                                      process="PCM Verbose Trace", message=line[:500]))
        calc.completed_at = datetime.utcnow()
        bom.status = "Calculated" if status == "Completed" else "Needs Review"
        db.commit()
    except Exception:
        db.rollback()
        if output_path.exists():
            output_path.unlink()
        raise
    db.refresh(calc)
    return calc
