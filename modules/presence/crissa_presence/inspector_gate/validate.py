"""Embedding record validation — pure, no IO."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .reasons import ReasonCode

FACE_DIM = 512
VOICE_DIM = 192


@dataclass
class ValidationResult:
    ok: bool
    reasons: list[ReasonCode] = field(default_factory=list)
    normalized: list[float] | None = None
    dim: int | None = None
    modality: str | None = None
    person_id: str | None = None
    model: str | None = None
    errors: list[str] = field(default_factory=list)


def _as_float_list(raw: Any) -> list[float] | None:
    if raw is None:
        return None
    try:
        out = [float(x) for x in raw]
    except (TypeError, ValueError):
        return None
    return out


def l2_normalize(vec: list[float]) -> list[float]:
    s = math.sqrt(sum(x * x for x in vec))
    if s < 1e-12:
        return vec
    return [x / s for x in vec]


def validate_emb_record(
    rec: dict[str, Any],
    *,
    expect_person_id: str | None = None,
    accept_legacy: bool = True,
) -> ValidationResult:
    """Validate emb JSON shape. accept_legacy: allow name-shaped identity-store files."""
    r = ValidationResult(ok=False)
    if not isinstance(rec, dict):
        r.errors.append("not a dict")
        r.reasons.append(ReasonCode.VALIDATION_FAIL)
        return r

    pid = rec.get("person_id") or rec.get("id") or rec.get("name")
    if pid is not None:
        pid = str(pid).strip().lower()
    r.person_id = pid
    if expect_person_id and pid and pid != expect_person_id.strip().lower():
        r.errors.append(f"person_id mismatch {pid}!={expect_person_id}")

    modality = (rec.get("modality") or rec.get("kind") or "face").strip().lower()
    if modality in ("arcface", "face_emb", "the face engine"):
        modality = "face"
    if modality in ("ecapa", "speaker", "voice_emb"):
        modality = "voice"
    r.modality = modality

    enc = _as_float_list(rec.get("encoding") or rec.get("embedding") or rec.get("vector"))
    if not enc:
        r.errors.append("missing encoding")
        r.reasons.append(ReasonCode.VALIDATION_FAIL)
        return r

    if any(math.isnan(x) or math.isinf(x) for x in enc):
        r.errors.append("nan/inf in encoding")
        r.reasons.append(ReasonCode.VALIDATION_FAIL)
        return r

    dim = len(enc)
    r.dim = dim
    expect_dim = FACE_DIM if modality == "face" else VOICE_DIM if modality == "voice" else None
    if expect_dim and dim != expect_dim:
        # legacy may vary; hard fail only if clearly wrong
        if dim < 32:
            r.errors.append(f"dim {dim} too small")
            r.reasons.append(ReasonCode.VALIDATION_FAIL)
            return r
        if modality == "face" and dim not in (FACE_DIM, 512, 128):
            r.errors.append(f"unexpected face dim {dim}")
            # still allow if accept_legacy and dim >= 64
            if not accept_legacy or dim < 64:
                r.reasons.append(ReasonCode.VALIDATION_FAIL)
                return r

    nrm = math.sqrt(sum(x * x for x in enc))
    if nrm < 1e-6:
        r.errors.append("degenerate L2 norm")
        r.reasons.append(ReasonCode.VALIDATION_FAIL)
        return r

    model = rec.get("model") or rec.get("model_id") or rec.get("backbone")
    r.model = str(model) if model else None
    if not r.model and not accept_legacy:
        r.errors.append("missing model id")
        r.reasons.append(ReasonCode.VALIDATION_FAIL)
        return r

    det = rec.get("det_score")
    if det is not None:
        try:
            if float(det) < 0.0:
                r.errors.append("det_score < 0")
                r.reasons.append(ReasonCode.VALIDATION_FAIL)
                return r
        except (TypeError, ValueError):
            r.errors.append("bad det_score")
            r.reasons.append(ReasonCode.VALIDATION_FAIL)
            return r

    r.normalized = l2_normalize(enc)
    r.ok = True
    r.reasons.append(ReasonCode.VALIDATION_OK)
    return r
