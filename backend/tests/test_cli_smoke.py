"""test_cli_smoke.py — Gác cổng 42 lệnh CLI: không subcommand nào bị gãy import ngầm.

Chốt chặn này đảm bảo:
  - Mọi subcommand đều được đăng ký trong ArgumentParser (không thiếu, không thừa).
  - Các lệnh cốt lõi luôn tồn tại.
  - `build_parser()` có thể được gọi mà không gãy (import chain ngầm).
"""

import argparse
from ptck import build_parser


def test_all_cli_subcommands_registered():
    """Đảm bảo tất cả subcommands của CLI đều được đăng ký."""
    parser = build_parser()
    subactions = [
        a for a in parser._actions
        if isinstance(a, argparse._SubParsersAction)
    ]
    assert len(subactions) > 0, "LỖI CLI: Không tìm thấy subparser nào!"
    commands = list(subactions[0].choices.keys())
    assert len(commands) >= 40, (
        f"LỖI CLI: Thiếu lệnh! Chỉ tìm thấy {len(commands)} commands."
    )


def test_critical_cli_commands_exist():
    """Các lệnh cốt lõi bắt buộc phải tồn tại."""
    parser = build_parser()
    subactions = [
        a for a in parser._actions
        if isinstance(a, argparse._SubParsersAction)
    ]
    commands = set(subactions[0].choices.keys())
    critical = {"market", "regime", "flow-map", "erl-scan", "status",
                "snapshot", "serve", "daily-close", "confidence", "phase4",
                "init-agent", "scan", "gold", "silver"}
    missing = critical - commands
    assert not missing, f"CRITICAL CLI MISSING: {missing}"


def _walk_choices(parser, prefix="", seen=None):
    """Đệ quy tìm tất cả (command_path, func) trong subparsers."""
    if seen is None:
        seen = {}
    for a in parser._actions:
        if isinstance(a, argparse._SubParsersAction):
            for name, subparser in a.choices.items():
                full = f"{prefix}{name}"
                func = subparser.get_default("func")
                if func is not None:
                    seen[full] = func
                _walk_choices(subparser, f"{full} ", seen)
    return seen


def test_every_command_has_callable_func():
    """Mọi subcommand (kể cả lồng nhau) phải có func callable."""
    parser = build_parser()
    commands = _walk_choices(parser)
    assert len(commands) >= 40, (
        f"LỖI CLI: Chỉ tìm thấy {len(commands)} leaf commands!"
    )
    for path, func in commands.items():
        assert callable(func), (
            f"LỖI CLI: func của lệnh '{path}' không callable!"
        )
