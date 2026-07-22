from __future__ import annotations

import importlib
import os
from dataclasses import dataclass

from .delivery_worker import (
    HermesDeliverySendRequest,
    HermesPlatformAcknowledgement,
    RetryableHermesPlatformError,
    TerminalHermesPlatformError,
)


class InvalidHermesTelegramConfigurationError(RuntimeError):
    def __str__(self) -> str:
        return "Hermes Telegram delivery is not configured"


@dataclass(frozen=True, slots=True, repr=False)
class HermesTelegramSender:
    _token: str
    _chat_id: str
    _thread_id: int | None

    @classmethod
    def from_hermes_config(cls) -> HermesTelegramSender:
        send_command = importlib.import_module("hermes_cli.send_cmd")
        send_command._load_hermes_env()
        gateway = importlib.import_module("gateway.config")
        platform = gateway.Platform.TELEGRAM
        config = gateway.load_gateway_config()
        platform_config = config.platforms.get(platform)
        home = config.get_home_channel(platform)
        token = None if platform_config is None else platform_config.token
        if not token:
            raise InvalidHermesTelegramConfigurationError
        if home is None:
            allowed_users = tuple(
                item.strip()
                for item in os.getenv("TELEGRAM_ALLOWED_USERS", "").split(",")
                if item.strip()
            )
            if len(allowed_users) != 1 or not allowed_users[0].isdigit():
                raise InvalidHermesTelegramConfigurationError
            return cls(_token=token, _chat_id=allowed_users[0], _thread_id=None)
        if not home.chat_id:
            raise InvalidHermesTelegramConfigurationError
        try:
            thread_id = None if home.thread_id is None else int(home.thread_id)
        except ValueError:
            raise InvalidHermesTelegramConfigurationError from None
        return cls(_token=token, _chat_id=str(home.chat_id), _thread_id=thread_id)

    def send(self, request: HermesDeliverySendRequest) -> HermesPlatformAcknowledgement:
        telegram = importlib.import_module("telegram")
        telegram_error = importlib.import_module("telegram.error")
        model_tools = importlib.import_module("model_tools")
        try:
            message = model_tools._run_async(self._send(telegram, request))
        except (telegram_error.NetworkError, telegram_error.RetryAfter, telegram_error.TimedOut, TimeoutError):
            raise RetryableHermesPlatformError from None
        except telegram_error.TelegramError:
            raise TerminalHermesPlatformError from None
        message_id = str(message.message_id)
        if not message_id.isdigit():
            raise TerminalHermesPlatformError
        return HermesPlatformAcknowledgement(message_id=message_id)

    async def _send(self, telegram, request: HermesDeliverySendRequest):
        reply = None
        if request.reply_to_message_id is not None:
            try:
                reply = telegram.ReplyParameters(message_id=int(request.reply_to_message_id))
            except ValueError:
                raise TerminalHermesPlatformError from None
        bot = telegram.Bot(token=self._token)
        async with bot:
            return await bot.send_message(
                chat_id=self._chat_id,
                text=request.text,
                message_thread_id=self._thread_id,
                reply_parameters=reply,
                read_timeout=10,
                write_timeout=10,
                connect_timeout=10,
                pool_timeout=10,
            )
