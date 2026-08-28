"""Policy-free validation mechanics for versioned KiCad component assets."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from .pcb import inspect_footprint
from .sexp import children, parse, text


class ComponentManifestError(ValueError):
    """A component manifest cannot be inspected safely."""


@dataclass(frozen=True, slots=True)
class ComponentFinding:
    code: str
    severity: str
    message: str
    path: str = ""


@dataclass(frozen=True, slots=True)
class ComponentReport:
    component_id: str
    version: str
    status: str
    footprint: str
    selectable: bool
    findings: tuple[ComponentFinding, ...]

    @property
    def errors(self) -> int:
        return sum(item.severity == "error" for item in self.findings)

    @property
    def warnings(self) -> int:
        return sum(item.severity == "warning" for item in self.findings)

    def as_dict(self) -> dict[str, Any]:
        return {
            "component_id": self.component_id,
            "version": self.version,
            "status": self.status,
            "footprint": self.footprint,
            "selectable": self.selectable,
            "errors": self.errors,
            "warnings": self.warnings,
            "findings": [asdict(item) for item in self.findings],
        }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def pin_map_hash(pin_map: Iterable[dict[str, Any]]) -> str:
    """Hash only electrical identity, independent of display labels or ordering."""
    normalized = sorted(
        (
            {
                "symbol_pin": str(item.get("symbol_pin", "")),
                "pads": sorted(str(pad) for pad in item.get("pads") or []),
            }
            for item in pin_map
        ),
        key=lambda item: (item["symbol_pin"], item["pads"]),
    )
    encoded = json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _asset(root: Path, relative: object) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ComponentManifestError("component asset path must be a non-empty relative path")
    base = root.resolve()
    candidate = (base / relative).resolve()
    if not candidate.is_relative_to(base) or candidate.is_symlink():
        raise ComponentManifestError("component asset escapes the project root or is a symlink")
    return candidate


def _model_bindings(source: str) -> set[str]:
    root = parse(source)
    return {text(node, 1) for node in children(root, "model") if text(node, 1)}


def validate_component_manifest(
    manifest: dict[str, Any],
    project_root: Path,
    *,
    source_ids: Iterable[str] = (),
) -> ComponentReport:
    """Validate hashes and KiCad geometry without deciding project policy."""
    component_id = str(manifest.get("component_id", ""))
    version = str(manifest.get("version", ""))
    status = str(manifest.get("status", ""))
    electrical = manifest.get("electrical") or {}
    eda = manifest.get("eda") or {}
    footprint_id = str(eda.get("footprint", ""))
    findings: list[ComponentFinding] = []

    source_id = str((manifest.get("source") or {}).get("source_id", ""))
    known_sources = set(source_ids)
    if known_sources and source_id not in known_sources:
        findings.append(ComponentFinding(
            "COMPONENT-SOURCE-UNKNOWN", "error",
            f"source_id {source_id!r} is absent from the pinned source lock",
        ))

    assets = eda.get("assets") or {}
    footprint_asset = assets.get("footprint") or {}
    footprint_path: Path | None = None
    try:
        footprint_path = _asset(project_root, footprint_asset.get("path"))
    except ComponentManifestError as exc:
        findings.append(ComponentFinding("COMPONENT-ASSET-PATH", "error", str(exc)))
    footprint_source = ""
    if footprint_path is not None:
        if not footprint_path.is_file():
            findings.append(ComponentFinding(
                "COMPONENT-ASSET-MISSING", "error", "footprint asset does not exist",
                footprint_path.as_posix(),
            ))
        else:
            footprint_source = footprint_path.read_text(encoding="utf-8", errors="replace")
            expected_hash = str(footprint_asset.get("sha256", ""))
            actual_hash = sha256_file(footprint_path)
            if actual_hash != expected_hash:
                findings.append(ComponentFinding(
                    "COMPONENT-ASSET-HASH", "error",
                    f"footprint hash differs: expected {expected_hash}, got {actual_hash}",
                    footprint_path.as_posix(),
                ))

    pin_map = electrical.get("pin_map") or []
    expected_pin_hash = str(eda.get("pin_map_hash", ""))
    actual_pin_hash = pin_map_hash(pin_map)
    if expected_pin_hash != actual_pin_hash:
        findings.append(ComponentFinding(
            "COMPONENT-PINMAP-HASH", "error",
            f"pin map hash differs: expected {expected_pin_hash}, got {actual_pin_hash}",
        ))

    if footprint_source:
        try:
            footprint = inspect_footprint(footprint_source)
        except ValueError as exc:
            findings.append(ComponentFinding("COMPONENT-FOOTPRINT-PARSE", "error", str(exc)))
        else:
            expected_name = footprint_id.split(":", 1)[-1]
            if footprint.library_id != expected_name:
                findings.append(ComponentFinding(
                    "COMPONENT-FOOTPRINT-ID", "error",
                    f"manifest names {expected_name!r}, asset contains {footprint.library_id!r}",
                ))
            actual_pads = [pad.number for pad in footprint.pads]
            mapped_pads = [str(pad) for item in pin_map for pad in item.get("pads") or []]
            missing = sorted(set(actual_pads) - set(mapped_pads))
            unknown = sorted(set(mapped_pads) - set(actual_pads))
            duplicates = sorted({pad for pad in mapped_pads if mapped_pads.count(pad) > 1})
            if missing or unknown or duplicates or len(mapped_pads) != len(actual_pads):
                findings.append(ComponentFinding(
                    "COMPONENT-PAD-MAP", "error",
                    f"pad map must cover every physical pad once; missing={missing}, "
                    f"unknown={unknown}, duplicates={duplicates}",
                ))

        bindings = _model_bindings(footprint_source)
        for model in manifest.get("models") or []:
            binding = str(model.get("binding", ""))
            if binding not in bindings:
                findings.append(ComponentFinding(
                    "COMPONENT-MODEL-BINDING", "error",
                    f"footprint does not bind model {binding!r}",
                ))
            try:
                model_path = _asset(project_root, model.get("path"))
            except ComponentManifestError as exc:
                findings.append(ComponentFinding("COMPONENT-ASSET-PATH", "error", str(exc)))
                continue
            if not model_path.is_file():
                findings.append(ComponentFinding(
                    "COMPONENT-ASSET-MISSING", "error", "3D model asset does not exist",
                    model_path.as_posix(),
                ))
            elif sha256_file(model_path) != str(model.get("sha256", "")):
                findings.append(ComponentFinding(
                    "COMPONENT-ASSET-HASH", "error", "3D model hash differs",
                    model_path.as_posix(),
                ))

    verification = manifest.get("verification") or {}
    if verification.get("step_model_required") and not manifest.get("models"):
        findings.append(ComponentFinding(
            "COMPONENT-MODEL-REQUIRED", "error" if status == "qualified" else "warning",
            "component requires a verified 3D model but declares none",
        ))
    if status != "qualified":
        findings.append(ComponentFinding(
            "COMPONENT-NOT-QUALIFIED", "warning",
            f"status {status!r} is not selectable for a new design",
        ))

    errors = sum(item.severity == "error" for item in findings)
    return ComponentReport(
        component_id=component_id,
        version=version,
        status=status,
        footprint=footprint_id,
        selectable=status == "qualified" and errors == 0,
        findings=tuple(findings),
    )


def validate_component_file(
    manifest_path: Path,
    project_root: Path,
    *,
    source_ids: Iterable[str] = (),
) -> ComponentReport:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ComponentManifestError(f"cannot read component manifest {manifest_path}: {exc}") from exc
    if not isinstance(manifest, dict):
        raise ComponentManifestError("component manifest must be a JSON object")
    return validate_component_manifest(manifest, project_root, source_ids=source_ids)
