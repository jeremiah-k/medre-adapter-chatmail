import pytest

from medre.adapters.chatmail.cli import execute, main, parser, read_qr, selected_config
from conftest import NativeSession


def test_help_does_not_open_profiles(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    assert "chats" in capsys.readouterr().out


def test_default_paths_and_explicit_account_selection(monkeypatch, tmp_path):
    monkeypatch.setenv("MEDRE_HOME", str(tmp_path / "home"))
    options = parser().parse_args(["chats", "--adapter", "mail", "--account-id", "2"])
    config = selected_config(options)
    assert config.account_id == 2
    assert config.accounts_dir.endswith("/adapters/mail/chatmail/accounts")
    assert not (tmp_path / "home").exists()


async def test_accounts_and_chats_expose_identifiers_without_credentials(config):
    session = NativeSession(config)
    original = session.call

    async def call(name, *args):
        if name == "get_chatlist_entries":
            return [1, 10]
        if name == "get_basic_chat_info":
            return {
                "name": "Bridge",
                "isDeviceChat": False,
                "isContactRequest": False,
                "isEncrypted": True,
                "isSelfTalk": False,
                "chatType": "Group",
            }
        return await original(name, *args)

    session.call = call
    accounts = await execute(
        parser().parse_args(["accounts"]), config, session_factory=lambda _: session
    )
    assert accounts == [{"account_id": 1, "configured": True}]
    chats = await execute(
        parser().parse_args(["chats"]), config, session_factory=lambda _: session
    )
    assert chats == [
        {
            "chat_id": 10,
            "kind": "Group",
            "name": "Bridge",
            "accepted": True,
            "encrypted": True,
        }
    ]
    assert not session.started


async def test_init_is_idempotent_and_does_not_prompt_for_configured_accounts(
    config, monkeypatch
):
    monkeypatch.setattr(
        "medre.adapters.chatmail.cli.read_qr",
        lambda _: pytest.fail("unexpected prompt"),
    )
    session = NativeSession(config)
    result = await execute(
        parser().parse_args(["init"]), config, session_factory=lambda _: session
    )
    assert result["configured"] is True
    assert not any(name == "add_transport_from_qr" for name, _ in session.calls)


async def test_private_qr_is_sent_only_to_the_native_setup_call(config, monkeypatch):
    secret = "DCLOGIN:test-only-private-value"
    monkeypatch.setattr("medre.adapters.chatmail.cli.read_qr", lambda _: secret)
    session = NativeSession(config)
    original = session.call
    configured = False

    async def call(name, *args):
        nonlocal configured
        if name == "is_configured":
            return configured
        if name == "add_transport_from_qr":
            assert args == (1, secret)
            configured = True
            return None
        return await original(name, *args)

    session.call = call
    result = await execute(
        parser().parse_args(["init"]), config, session_factory=lambda _: session
    )
    assert secret not in str(result)


def test_noninteractive_qr_requires_explicit_stdin(monkeypatch):
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO("DCACCOUNT:relay.test\n"))
    with pytest.raises(ValueError, match="qr-stdin"):
        read_qr(parser().parse_args(["init"]))
    assert (
        read_qr(parser().parse_args(["init", "--qr-stdin"])) == "DCACCOUNT:relay.test"
    )


async def test_join_timeout_reports_pending_chat_id(config, monkeypatch):
    from dataclasses import replace

    session = NativeSession(config)
    original = session.call

    async def call(name, *args):
        if name == "secure_join":
            return 17
        if name == "can_send":
            return False
        return await original(name, *args)

    session.call = call
    monkeypatch.setattr(
        "medre.adapters.chatmail.cli.read_qr", lambda _: "OPENPGP4FPR:test"
    )
    with pytest.raises(ValueError, match="pending for chat 17"):
        await execute(
            parser().parse_args(["join"]),
            replace(config, rpc_timeout_seconds=0.015),
            session_factory=lambda _: session,
        )
    assert not session.started


def test_log_and_idle_batches_do_not_trigger_full_history_scans():
    from medre.adapters.chatmail.session import _should_wake

    assert not _should_wake(
        [
            {"event": {"kind": "Info", "msg": "diagnostic"}},
            {"event": {"kind": "ImapInboxIdle"}},
        ]
    )
    assert _should_wake([{"event": {"kind": "IncomingMsg", "chatId": 10}}])
    assert _should_wake([{"event": {"kind": "SmtpMessageSent"}}])
    assert _should_wake([{"event": {"kind": "NewMessageType"}}])
    assert _should_wake([None])


def test_profile_environment_overrides_apply_without_a_yaml_file(monkeypatch, tmp_path):
    monkeypatch.setenv("MEDRE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv(
        "MEDRE_ADAPTER__MAIL__ACCOUNTS_DIR", str(tmp_path / "private-profile")
    )
    monkeypatch.setenv("MEDRE_ADAPTER__MAIL__ACCOUNT_ID", "4")
    config = selected_config(parser().parse_args(["accounts", "--adapter", "mail"]))
    assert config.account_id == 4
    assert config.accounts_dir == str(tmp_path / "private-profile")
    assert not (tmp_path / "home").exists()


@pytest.mark.parametrize(
    "error", [ValueError("private-qr-value"), RuntimeError("private-password-value")]
)
def test_unknown_exception_text_is_not_printed(monkeypatch, capsys, error):
    def fail(_):
        raise error

    monkeypatch.setattr("medre.adapters.chatmail.cli.selected_config", fail)
    assert main(["chats"]) == 1
    assert "private-" not in capsys.readouterr().err


async def test_private_profile_prompt_has_no_running_native_owner(config, monkeypatch):
    session = NativeSession(config)
    original = session.call
    configured = False

    async def call(name, *args):
        nonlocal configured
        if name == "is_configured":
            return configured
        if name == "add_transport_from_qr":
            configured = True
            return None
        return await original(name, *args)

    def prompt(_):
        assert not session.started
        return "DCLOGIN:private-fixture"

    session.call = call
    monkeypatch.setattr("medre.adapters.chatmail.cli.read_qr", prompt)
    await execute(
        parser().parse_args(["init"]), config, session_factory=lambda _: session
    )


def test_discovery_escapes_terminal_controls_in_peer_names(monkeypatch, capsys):
    import medre.adapters.chatmail.cli as cli

    async def output(*args):
        return [{"chat_id": 10, "name": "Eve\x1b[2J\u009b31m\nspoof"}]

    monkeypatch.setattr(cli, "execute", output)
    monkeypatch.setattr(cli, "selected_config", lambda _: None)
    assert main(["chats"]) == 0
    text = capsys.readouterr().out
    assert "\x1b" not in text and "\u009b" not in text
    assert "chat_id=10" in text


def test_json_discovery_escapes_c1_controls_without_changing_values(
    monkeypatch, capsys
):
    import json
    import medre.adapters.chatmail.cli as cli

    async def output(*args):
        return [{"chat_id": 10, "name": "Eve\u009b31m"}]

    monkeypatch.setattr(cli, "selected_config", lambda _: None)
    monkeypatch.setattr(cli, "execute", output)
    assert cli.main(["chats", "--json"]) == 0
    text = capsys.readouterr().out
    assert "\u009b" not in text
    assert json.loads(text)[0]["name"] == "Eve\u009b31m"


@pytest.mark.parametrize(
    "qr",
    [
        "https://i.delta.chat/#private",
        "https://i.delta.chat#private",
        "OPENPGP4FPR:private",
    ],
)
async def test_invitation_uri_forms_reach_native_join(config, monkeypatch, qr):
    monkeypatch.setattr("medre.adapters.chatmail.cli.read_qr", lambda _: qr)
    session = NativeSession(config)
    original = session.call

    async def call(name, *args):
        if name == "secure_join":
            assert args == (1, qr)
            return 10
        if name == "can_send":
            return True
        return await original(name, *args)

    session.call = call
    result = await execute(
        parser().parse_args(["join"]), config, session_factory=lambda _: session
    )
    assert result["chat_id"] == 10


def test_invalid_account_reports_safe_error(capsys):
    assert main(["chats", "--account-id", "0"]) == 1
    assert "account_id must be a positive account ID" in capsys.readouterr().err


def test_public_profile_directory_is_rejected_without_chmod(tmp_path):
    import os
    from medre.adapters.chatmail.cli import create_profile_directory, UsageError

    if os.name != "posix":
        pytest.skip("POSIX permissions")
    directory = tmp_path / "public-profile"
    directory.mkdir(mode=0o755)
    with pytest.raises(UsageError, match="only to its owner"):
        create_profile_directory(str(directory))
    assert directory.stat().st_mode & 0o077
