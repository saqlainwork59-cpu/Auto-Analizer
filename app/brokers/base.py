"""Real-broker execution architecture - DISABLED by default.

Execution requires *all* of the following, checked server-side on every order:
  1. deploy-time env flag BROKER_EXECUTION_ENABLED=true
  2. admin kill-switch `broker_execution_enabled` on
  3. the user has linked encrypted broker credentials and opted in
  4. the user re-authenticates with their password for the order (step-up auth)
  5. an explicit, single-use order confirmation token issued for exactly this order preview
Signals never place orders on their own: there is no call path from the signal engine to here.
No concrete broker adapter ships enabled; adapters implement `BrokerAdapter` and are registered below.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


class ExecutionDisabled(PermissionError):
    pass


@dataclass(frozen=True)
class OrderRequest:
    user_id: int
    symbol: str
    side: str            # BUY | SELL
    quantity: float
    order_type: str      # market | limit
    limit_price: float | None
    stop_loss: float | None
    take_profit: float | None
    client_order_id: str


@dataclass(frozen=True)
class OrderResult:
    broker_order_id: str
    status: str
    raw: dict


class BrokerAdapter(ABC):
    name: str

    @abstractmethod
    async def validate_credentials(self, api_key: str, api_secret: str | None) -> bool: ...

    @abstractmethod
    async def place_order(self, req: OrderRequest, api_key: str, api_secret: str | None) -> OrderResult: ...

    @abstractmethod
    async def cancel_order(self, broker_order_id: str, api_key: str, api_secret: str | None) -> None: ...


ADAPTERS: dict[str, BrokerAdapter] = {}  # intentionally empty


async def execution_gate(env_enabled: bool, admin_enabled: bool, user_opted_in: bool, reauthenticated: bool,
                         confirmation_valid: bool) -> None:
    checks = [
        (env_enabled, "real-money execution is disabled on this deployment"),
        (admin_enabled, "real-money execution is disabled by an administrator"),
        (user_opted_in, "you have not enabled live execution for your account"),
        (reauthenticated, "password re-authentication required for each live order"),
        (confirmation_valid, "order confirmation token missing, expired or already used"),
    ]
    for ok, msg in checks:
        if not ok:
            raise ExecutionDisabled(msg)
