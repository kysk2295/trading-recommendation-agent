from __future__ import annotations

import hashlib

from trading_agent.future_session_coordinator_inspectors import CoordinatorInspectionError, inspect_request
from trading_agent.future_session_coordinator_service_models import (
    FutureSessionCoordinatorServiceConfig,
)
from trading_agent.future_session_plan_models import (
    FutureSessionMarket,
    FutureSessionPlanRequest,
    canonical_request_json,
)


class FutureSessionTemplateAuthorityError(ValueError):
    pass


def inspect_bound_template(
    config: FutureSessionCoordinatorServiceConfig,
    market: FutureSessionMarket,
) -> FutureSessionPlanRequest:
    path = config.us_template_request_path if market is FutureSessionMarket.US else config.kr_template_request_path
    expected = config.us_template_sha256 if market is FutureSessionMarket.US else config.kr_template_sha256
    try:
        template = inspect_request(path)
    except CoordinatorInspectionError:
        raise FutureSessionTemplateAuthorityError from None
    actual = hashlib.sha256(canonical_request_json(template).encode()).hexdigest()
    if actual != expected or template.market is not market:
        raise FutureSessionTemplateAuthorityError
    return template


def verify_bound_templates(config: FutureSessionCoordinatorServiceConfig) -> None:
    _ = inspect_bound_template(config, FutureSessionMarket.US)
    _ = inspect_bound_template(config, FutureSessionMarket.KR)


__all__ = (
    "FutureSessionTemplateAuthorityError",
    "inspect_bound_template",
    "verify_bound_templates",
)
