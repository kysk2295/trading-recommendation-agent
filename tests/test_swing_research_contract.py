from __future__ import annotations

import ast
import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from trading_agent.experiment_scope_models import (
    ExperimentScope as PureExperimentScope,
)
from trading_agent.experiment_scope_models import (
    ExperimentScopeKind as PureExperimentScopeKind,
)
from trading_agent.lane_contract_models import ExperimentScope, ExperimentScopeKind
from trading_agent.lane_identity_models import LaneId as PureLaneId
from trading_agent.lane_policy_models import LaneId
from trading_agent.swing_new_high_rvol import NewHighRvolConfig
from trading_agent.swing_research_contract import SWING_RESEARCH_CONTRACT

PROJECT_ROOT = Path(__file__).parents[1]
EXAMPLE_MANIFEST = PROJECT_ROOT / "examples" / "research" / "us-swing-new-high-rvol-v1.json"
_FORBIDDEN_IMPORT_MARKERS = (
    "alpaca",
    "paper",
    "broker",
    "execution",
    "credential",
    "provider",
    "lifecycle_controller",
    "portfolio_manager",
)


def test_swing_contract_matches_the_source_bound_hypothesis_card() -> None:
    contract = SWING_RESEARCH_CONTRACT
    manifest = json.loads(EXAMPLE_MANIFEST.read_text(encoding="utf-8"))

    assert contract.hypothesis_id == manifest["experiment_scope"]["hypothesis_id"]
    assert contract.hypothesis == manifest["hypothesis"]
    assert contract.falsification_rule == manifest["falsification_rule"]
    assert contract.strategy_id == "new_high_momentum"
    assert contract.strategy_version == "new_high_rvol_20d_1p5_v1"
    assert contract.experiment_scope == ExperimentScope.model_validate(manifest["experiment_scope"])


def test_swing_contract_is_immutable_and_explicit_about_shadow_only_research() -> None:
    contract = SWING_RESEARCH_CONTRACT
    config = NewHighRvolConfig()

    assert contract.parameter_set == (
        f"entry_buffer_bps={config.entry_buffer_bps}",
        f"lookback_sessions={config.lookback_sessions}",
        f"max_holding_sessions={config.max_holding_sessions}",
        f"minimum_rvol={config.minimum_rvol}",
        f"stop_loss_bps={config.stop_loss_bps}",
        f"target_r_multiple={config.target_r_multiple}",
    )
    assert contract.data_contract == (
        "completed_daily_ohlcv_only=true",
        "point_in_time_source=true",
        "source=swing_shadow_daily_source",
    )
    assert contract.cost_model == ("execution_costs=not_modeled",)
    assert contract.portfolio_policy == (
        "broker_orders=false",
        "mode=shadow_only",
        "order_submission=false",
    )

    with pytest.raises(FrozenInstanceError):
        contract.strategy_version = "changed"  # type: ignore[misc]


def test_legacy_contract_imports_retain_pure_primitive_identity() -> None:
    assert LaneId is PureLaneId
    assert ExperimentScope is PureExperimentScope
    assert ExperimentScopeKind is PureExperimentScopeKind


@pytest.mark.parametrize(
    "root_module",
    ("trading_agent.swing_research_contract", "trading_agent.experiment_ledger_models"),
)
def test_research_contract_import_closures_exclude_operational_modules(root_module: str) -> None:
    reachable_modules = _reachable_local_trading_agent_modules(root_module)

    assert not {
        module
        for module in reachable_modules
        if any(marker in module for marker in _FORBIDDEN_IMPORT_MARKERS)
    }


def _reachable_local_trading_agent_modules(root_module: str) -> set[str]:
    reachable: set[str] = set()
    pending = [root_module]
    while pending:
        module_name = pending.pop()
        if module_name in reachable:
            continue
        module_path = _local_module_path(module_name)
        if module_path is None:
            continue
        reachable.add(module_name)
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        pending.extend(_local_imported_modules(module_name, module_path, tree))
    return reachable


def _local_module_path(module_name: str) -> Path | None:
    relative_path = Path(*module_name.split("."))
    module_path = PROJECT_ROOT / relative_path.with_suffix(".py")
    if module_path.is_file():
        return module_path
    package_path = PROJECT_ROOT / relative_path / "__init__.py"
    return package_path if package_path.is_file() else None


def _local_imported_modules(module_name: str, module_path: Path, tree: ast.Module) -> set[str]:
    imported_modules: set[str] = set()
    for node in _runtime_import_nodes(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names if alias.name.startswith("trading_agent"))
        elif isinstance(node, ast.ImportFrom):
            imported_modules.update(_resolve_from_import(module_name, module_path, node))
    return {candidate for candidate in imported_modules if _local_module_path(candidate) is not None}


def _runtime_import_nodes(tree: ast.Module) -> list[ast.Import | ast.ImportFrom]:
    collector = _RuntimeImportCollector()
    collector.visit(tree)
    return collector.imports


class _RuntimeImportCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.imports: list[ast.Import | ast.ImportFrom] = []

    def visit_Import(self, node: ast.Import) -> None:
        self.imports.append(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.imports.append(node)

    def visit_If(self, node: ast.If) -> None:
        if _is_type_checking_test(node.test):
            for statement in node.orelse:
                self.visit(statement)
            return
        self.generic_visit(node)


def _is_type_checking_test(test: ast.expr) -> bool:
    return isinstance(test, ast.Name) and test.id == "TYPE_CHECKING"


def _resolve_from_import(module_name: str, module_path: Path, node: ast.ImportFrom) -> set[str]:
    if node.level:
        package_parts = module_name.split(".") if module_path.name == "__init__.py" else module_name.split(".")[:-1]
        base_parts = package_parts[: len(package_parts) - node.level + 1]
        base_module = ".".join((*base_parts, *(node.module.split(".") if node.module else ())))
    else:
        base_module = node.module or ""
    if base_module != "trading_agent" and not base_module.startswith("trading_agent."):
        return set()
    return {base_module, *(f"{base_module}.{alias.name}" for alias in node.names)}
