from __future__ import annotations

import ast
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final, override


@dataclass(frozen=True, slots=True)
class KrDayReadOnlyCapability:
    provider: str
    module: str
    method: str
    path: str
    tr_id: str | None = None
    ws_tr_type: str | None = None
    ws_tr_cd: str | None = None
    ws_tr_key: str | None = None


@dataclass(frozen=True, slots=True)
class KrDayReadOnlyEvidenceBoundary:
    capabilities: tuple[KrDayReadOnlyCapability, ...]
    source_files: tuple[Path, ...]


class KrDayReadOnlyBoundaryError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR Day provider capability boundary is not read-only and closed"


KR_DAY_READ_ONLY_CAPABILITIES: Final = (
    KrDayReadOnlyCapability(
        provider="kis",
        module="trading_agent.kis_kr_market_client",
        method="GET",
        path="/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice",
        tr_id="FHKST03010200",
    ),
    KrDayReadOnlyCapability(
        provider="kis",
        module="trading_agent.kis_kr_market_client",
        method="GET",
        path="/uapi/domestic-stock/v1/quotations/inquire-price",
        tr_id="FHKST01010100",
    ),
    KrDayReadOnlyCapability(
        provider="kis",
        module="trading_agent.kis_kr_market_client",
        method="GET",
        path="/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn",
        tr_id="FHKST01010200",
    ),
    KrDayReadOnlyCapability(
        provider="kis",
        module="trading_agent.kis_kr_session_calendar_client",
        method="GET",
        path="/uapi/domestic-stock/v1/quotations/chk-holiday",
        tr_id="CTCA0903R",
    ),
    KrDayReadOnlyCapability(
        provider="kis",
        module="trading_agent.kis_kr_ranking",
        method="GET",
        path="/uapi/domestic-stock/v1/ranking/fluctuation",
        tr_id="FHPST01700000",
    ),
    KrDayReadOnlyCapability(
        provider="kis",
        module="trading_agent.kis_kr_ranking",
        method="GET",
        path="/uapi/domestic-stock/v1/quotations/volume-rank",
        tr_id="FHPST01710000",
    ),
    KrDayReadOnlyCapability(
        provider="ls",
        module="trading_agent.ls_nws_stream",
        method="WSS_SEND",
        path="wss://openapi.ls-sec.co.kr:9443/websocket",
        ws_tr_type="3",
        ws_tr_cd="NWS",
        ws_tr_key="NWS001",
    ),
    KrDayReadOnlyCapability(
        provider="opendart",
        module="trading_agent.opendart_client",
        method="GET",
        path="/api/list.json",
    ),
)

_FORBIDDEN_IMPORT_STEM_PREFIXES: Final = (
    "paper_",
    "alpaca_",
    "broker_",
    "execution_",
)
_FORBIDDEN_IMPORT_SEGMENTS: Final = frozenset({"account", "balance", "order", "position", "execution"})
_FORBIDDEN_SYMBOLS: Final = frozenset({"PaperOrderAdmissionRequest", "PaperMutationArm"})
_FORBIDDEN_PATHS: Final = (
    "/stock/" + "accno",
    "/stock/" + "order",
)

type SourceScan = Callable[[Path], tuple[Path, ...]]


def require_kr_day_read_only_boundary(
    capabilities: tuple[KrDayReadOnlyCapability, ...] = KR_DAY_READ_ONLY_CAPABILITIES,
    *,
    source_root: Path = Path(__file__).parent,
    _source_scan: SourceScan | None = None,
) -> KrDayReadOnlyEvidenceBoundary:
    if len(capabilities) != len(set(capabilities)) or capabilities != KR_DAY_READ_ONLY_CAPABILITIES:
        raise KrDayReadOnlyBoundaryError
    scan = verify_kr_day_source_closure if _source_scan is None else _source_scan
    return KrDayReadOnlyEvidenceBoundary(
        capabilities=capabilities,
        source_files=scan(source_root),
    )


def verify_kr_day_source_closure(source_root: Path) -> tuple[Path, ...]:
    files = tuple(
        sorted(
            {
                *source_root.glob("kr_day_*.py"),
                *source_root.glob("kr_theme_day_*.py"),
            }
        )
    )
    if not files:
        raise KrDayReadOnlyBoundaryError
    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError, UnicodeError):
            raise KrDayReadOnlyBoundaryError from None
        if _unsafe_tree(tree):
            raise KrDayReadOnlyBoundaryError
    return files


def _unsafe_tree(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(_unsafe_import(alias.name) for alias in node.names):
            return True
        if isinstance(node, ast.ImportFrom):
            if node.module is not None and _unsafe_import(node.module):
                return True
            if any(alias.name in _FORBIDDEN_SYMBOLS for alias in node.names):
                return True
        if isinstance(node, (ast.Name, ast.Attribute)):
            name = node.id if isinstance(node, ast.Name) else node.attr
            if name in _FORBIDDEN_SYMBOLS:
                return True
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and any(path in node.value.lower() for path in _FORBIDDEN_PATHS)
        ):
            return True
        if isinstance(node, ast.Dict) and _unsafe_ws_registration(node):
            return True
    return False


def _unsafe_import(module: str) -> bool:
    lowered = module.lower()
    normalized_segments = tuple(lowered.replace("-", "_").split("."))
    stem = normalized_segments[-1]
    segments = frozenset(normalized_segments)
    return stem.startswith(_FORBIDDEN_IMPORT_STEM_PREFIXES) or bool(segments & _FORBIDDEN_IMPORT_SEGMENTS)


def _unsafe_ws_registration(node: ast.Dict) -> bool:
    for key, value in zip(node.keys, node.values, strict=True):
        if (
            isinstance(key, ast.Constant)
            and key.value == "tr_type"
            and isinstance(value, ast.Constant)
            and value.value in {"1", "2", 1, 2}
        ):
            return True
    return False


__all__ = (
    "KR_DAY_READ_ONLY_CAPABILITIES",
    "KrDayReadOnlyBoundaryError",
    "KrDayReadOnlyCapability",
    "KrDayReadOnlyEvidenceBoundary",
    "require_kr_day_read_only_boundary",
    "verify_kr_day_source_closure",
)
