from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models.workbook import WorkbookType
from app.pcm.master_data.static_importer import import_support_workbook
from app.pcm.master_data.output_matrix import columns_for_commodities
from app.services.pcm_calculation import definition_with_output_matrix_rows


ROOT = Path(__file__).resolve().parents[2]


def test_master_output_matrix_imports_dynamic_columns_and_rows():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        version = import_support_workbook(
            db,
            code="master_output_matrix",
            name="Master Output Matrix",
            description="Legacy output mapping",
            path=ROOT / "PCM" / "Master Output Matrix.xlsx",
            sheet_name="Master output Final",
            tab_name="Master output Final",
            header_row=1,
            replace=False,
        )
        db.commit()
        workbook = db.query(WorkbookType).filter_by(code="master_output_matrix").one()
        tab = workbook.tabs[0]
        assert version == 1
        assert len(tab.columns) >= 20
        assert len([row for row in tab.rows if not row.is_deleted]) >= 90
        first = tab.rows[0].row_data
        assert first["Final Out Put Sheet"] == "PF Code"
        assert first["BOM"] == "Y"
        assert "Hard Make" in first
    engine.dispose()


def test_columns_for_commodities_uses_nonblank_flags():
    rows = [
        {"Final Out Put Sheet": "PF Code", "BOM": "Y", "Hard Make": "PF Code"},
        {"Final Out Put Sheet": "Total Cost/Pc", "BOM": None, "Hard Make": "Done"},
        {"Final Out Put Sheet": "Seat_Function", "BOM": None, "Seat": "Y"},
        {"Final Out Put Sheet": "Ignored", "BOM": None, "Hard Make": None},
    ]

    assert columns_for_commodities(rows, ["Hard Make"]) == ["PF Code", "Total Cost/Pc"]


def test_definition_with_output_matrix_rows_snapshots_selected_columns():
    definition = {
        "output": {
            "master_sheet": "Master_Output",
            "profile_master_output_columns": {
                "hard": "Hard Make",
                "buy_std": "Buy Std",
            },
        }
    }
    results = [{"profile": "hard"}, {"profile": "buy_std"}]
    rows = [
        {"Final Out Put Sheet": "PF Code", "BOM": "Y", "Hard Make": "PF Code", "Buy Std": "PF Code"},
        {"Final Out Put Sheet": "Total Cost/Pc", "Hard Make": "Done"},
        {"Final Out Put Sheet": "Total/Pc", "Buy Std": "Done"},
        {"Final Out Put Sheet": "Seat_Function", "Seat": "Y"},
    ]

    configured = definition_with_output_matrix_rows(definition, results, rows)

    assert configured is not definition
    assert configured["output"]["master_output_matrix_rows"] == rows
    assert configured["output"]["master_output_columns"] == ["PF Code", "Total Cost/Pc", "Total/Pc"]
    assert configured["output"]["master_output_matrix_profiles"] == ["buy_std", "hard"]


def test_output_matrix_profile_mapping_is_definition_driven():
    definition = {
        "output": {
            "master_sheet": "Master_Output",
            "profile_master_output_columns": {"new_profile": "Seat"},
        }
    }
    rows = [
        {"Final Out Put Sheet": "PF Code", "BOM": "Y"},
        {"Final Out Put Sheet": "Seat_Function", "Seat": "Y"},
        {"Final Out Put Sheet": "Hard Only", "Hard Make": "Y"},
    ]

    configured = definition_with_output_matrix_rows(definition, [{"profile": "new_profile"}], rows)

    assert configured["output"]["master_output_columns"] == ["PF Code", "Seat_Function"]
