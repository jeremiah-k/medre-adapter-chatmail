"""Pinned RPC contract for outgoing native quotes (no external mail service)."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from medre.testing.sdk_contract import assert_installed_sdk_pins

pytestmark = pytest.mark.chatmail_sdk


def test_pinned_rpc_client_exposes_quoted_message_field():
    """Native quote ID is transmitted in the released send_msg draft field."""
    from deltachat_rpc_client.chat import Chat

    assert_installed_sdk_pins(
        "medre-adapter-chatmail", ("deltachat-rpc-client", "deltachat-rpc-server")
    )
    send = Mock(return_value=31)
    chat = Chat(SimpleNamespace(id=1, _rpc=SimpleNamespace(send_msg=send)), 10)
    result = chat.send_message(text="[Alice/Matrix] A Unicode reply ☃", quoted_msg=19)
    assert result.id == 31
    args = send.call_args.args
    assert args[:2] == (1, 10)
    assert args[2]["quotedMessageId"] == 19
    assert args[2]["text"] == "[Alice/Matrix] A Unicode reply ☃"
