from __future__ import annotations

import socket
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager

import httpx2

from trading_agent.alpaca_paper_config import (
    ALPACA_PAPER_TRADING_URL,
    AlpacaPaperCredentials,
)
from trading_agent.alpaca_paper_mutation_client import AlpacaPaperMutationClient
from trading_agent.paper_mutation_executor import PaperMutationBroker

type PaperMutationBrokerOpener = Callable[
    [AlpacaPaperCredentials],
    AbstractContextManager[PaperMutationBroker],
]


@contextmanager
def open_alpaca_paper_mutation_broker(
    credentials: AlpacaPaperCredentials,
) -> Iterator[PaperMutationBroker]:
    limits = httpx2.Limits(
        max_connections=1,
        max_keepalive_connections=1,
        keepalive_expiry=10.0,
    )
    transport = httpx2.HTTPTransport(
        http2=True,
        retries=0,
        limits=limits,
        socket_options=[(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)],
    )
    with httpx2.Client(
        base_url=ALPACA_PAPER_TRADING_URL,
        transport=transport,
        timeout=httpx2.Timeout(connect=5.0, read=10.0, write=10.0, pool=5.0),
        follow_redirects=False,
    ) as client:
        yield AlpacaPaperMutationClient(client, credentials)
