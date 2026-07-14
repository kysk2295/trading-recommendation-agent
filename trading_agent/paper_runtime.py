from __future__ import annotations

import datetime as dt
from collections.abc import Callable

from trading_agent.alpaca_paper_client import AlpacaPaperClient
from trading_agent.alpaca_paper_config import (
    AlpacaPaperCredentials,
    create_alpaca_paper_read_client,
)
from trading_agent.paper_execution_models import PaperBrokerState

type CredentialLoader = Callable[[], AlpacaPaperCredentials]
type PaperStateLoader = Callable[[AlpacaPaperCredentials], PaperBrokerState]


def read_paper_broker_state(
    credentials: AlpacaPaperCredentials,
) -> PaperBrokerState:
    with create_alpaca_paper_read_client() as http_client:
        client = AlpacaPaperClient(http_client, credentials)
        account = client.account(dt.datetime.now(dt.UTC))
        return PaperBrokerState(
            account=account,
            open_orders=client.open_orders(),
            positions=client.positions(),
        )
