from __future__ import annotations

import json
from pathlib import Path

import pytest

from twin_kicad import (
    ComponentManifestError,
    inspect_footprint,
    pin_map_hash,
    sha256_file,
    validate_component_file,
    validate_component_manifest,
)

FOOTPRINT = '''(footprint "SW_TEST"
  (layer "F.Cu")
  (fp_line (start -2 -2) (end 2 -2) (layer "F.SilkS"))
  (pad "1" smd rect (at -2 -1) (size 2 1) (layers "F.Cu" "F.Paste" "F.Mask"))
  (pad "2" smd rect (at 2 -1) (size 2 1) (layers "F.Cu" "F.Paste" "F.Mask"))
  (pad "3" smd rect (at -2 1) (size 2 1) (layers "F.Cu" "F.Paste" "F.Mask"))
  (pad "4" smd rect (at 2 1) (size 2 1) (layers "F.Cu" "F.Paste" "F.Mask"))
  (model "${KIPRJMOD}/models/SW_TEST.step" (offset (xyz 0 0 0)) (scale (xyz 1 1 1)))
)'''


def _manifest(root: Path, *, status: str = "qualified") -> dict:
    footprint = root / "canonical.pretty" / "SW_TEST.kicad_mod"
    model = root / "models" / "SW_TEST.step"
    footprint.parent.mkdir()
    model.parent.mkdir()
    footprint.write_text(FOOTPRINT, encoding="utf-8")
    model.write_bytes(b"STEP model")
    pins = [
        {"symbol_pin": "1", "pads": ["1", "3"]},
        {"symbol_pin": "2", "pads": ["2", "4"]},
    ]
    return {
        "component_id": "switch-test",
        "version": "1.0.0",
        "status": status,
        "source": {"source_id": "vendor"},
        "electrical": {"pin_map": pins},
        "eda": {
            "footprint": "canonical:SW_TEST",
            "pin_map_hash": pin_map_hash(pins),
            "assets": {
                "footprint": {
                    "path": "canonical.pretty/SW_TEST.kicad_mod",
                    "sha256": sha256_file(footprint),
                }
            },
        },
        "models": [{
            "path": "models/SW_TEST.step",
            "sha256": sha256_file(model),
            "binding": "${KIPRJMOD}/models/SW_TEST.step",
        }],
        "verification": {"step_model_required": True},
    }


def test_inspect_standalone_footprint() -> None:
    footprint = inspect_footprint(FOOTPRINT)
    assert footprint.library_id == "SW_TEST"
    assert [pad.number for pad in footprint.pads] == ["1", "2", "3", "4"]


def test_component_assets_pin_map_and_model_binding_pass(tmp_path: Path) -> None:
    report = validate_component_manifest(_manifest(tmp_path), tmp_path, source_ids={"vendor"})
    assert report.errors == 0
    assert report.selectable is True
    assert report.as_dict()["footprint"] == "canonical:SW_TEST"


def test_component_reports_hash_and_pad_map_regressions(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    manifest["eda"]["assets"]["footprint"]["sha256"] = "sha256:" + "0" * 64
    manifest["electrical"]["pin_map"][0]["pads"].append("2")
    manifest["eda"]["pin_map_hash"] = pin_map_hash(manifest["electrical"]["pin_map"])
    report = validate_component_manifest(manifest, tmp_path, source_ids={"vendor"})
    assert {item.code for item in report.findings} >= {
        "COMPONENT-ASSET-HASH",
        "COMPONENT-PAD-MAP",
    }
    assert report.selectable is False


def test_component_refuses_asset_path_outside_project(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    manifest["eda"]["assets"]["footprint"]["path"] = "../outside.kicad_mod"
    report = validate_component_manifest(manifest, tmp_path, source_ids={"vendor"})
    assert report.findings[0].code == "COMPONENT-ASSET-PATH"


def test_provisional_component_is_integral_but_not_selectable(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, status="provisional")
    report = validate_component_manifest(manifest, tmp_path, source_ids={"vendor"})
    assert report.errors == 0
    assert report.selectable is False
    assert any(item.code == "COMPONENT-NOT-QUALIFIED" for item in report.findings)


def test_component_file_requires_json_object(tmp_path: Path) -> None:
    path = tmp_path / "component.json"
    path.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")
    with pytest.raises(ComponentManifestError, match="JSON object"):
        validate_component_file(path, tmp_path)
