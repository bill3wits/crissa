"""Inspector pure gate — stamp + store assurance stack.

Zero rail / Crissa imports. Used by rail-presence wiring later.
Design: docs/designs/design-inspector-emb-safety.md
"""

from .reasons import ReasonCode
from .validate import validate_emb_record, ValidationResult
from .gate import (
    GateConfig,
    PersonBank,
    RosterPerson,
    Verdict,
    evaluate_verdict,
    assign_closed_population,
    cross_bank_contamination,
)
from .hear import (
    HearVerdict,
    az_to_clock,
    desk_check_ok,
    join_hear,
    residual_from_door,
    residual_from_pair,
    snap_legal_beam,
)

__all__ = [
    "ReasonCode",
    "validate_emb_record",
    "ValidationResult",
    "GateConfig",
    "PersonBank",
    "RosterPerson",
    "Verdict",
    "evaluate_verdict",
    "assign_closed_population",
    "cross_bank_contamination",
    "HearVerdict",
    "az_to_clock",
    "desk_check_ok",
    "join_hear",
    "residual_from_door",
    "residual_from_pair",
    "snap_legal_beam",
]
