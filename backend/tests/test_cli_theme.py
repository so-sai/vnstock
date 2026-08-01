"""test_cli_theme.py — TDD cho zero-dependency ANSI Terminal Styling module (cli_theme.py).

Kiểm thử:
  - Khởi tạo VT100 trên Windows an toàn không crash
  - Định dạng màu sắc ANSI và tự động RESET
  - Tự động tắt ANSI khi phát hiện cờ môi trường NO_COLOR
  - Helper color_mos() và color_status()
"""

import os
import sys
import pytest


def test_init_terminal_colors_no_crash():
    from utils.cli_theme import init_terminal_colors
    # Mở VT100 mode không văng lỗi
    init_terminal_colors()


def test_color_formatting_and_reset():
    from utils.cli_theme import Color, c_red, c_green, c_yellow, c_cyan, c_dim

    os.environ.pop("NO_COLOR", None)
    res_red = c_red("ALERT")
    res_green = c_green("PASS")

    assert "ALERT" in res_red
    assert "PASS" in res_green
    assert Color.RESET in res_red
    assert Color.RESET in res_green


def test_no_color_strips_ansi_codes(monkeypatch):
    from utils import cli_theme
    monkeypatch.setenv("NO_COLOR", "1")

    res = cli_theme.c_red("ALERT")
    # Khi có NO_COLOR -> trả về chuỗi thuần không dán mã ANSI
    assert res == "ALERT"
    assert "\033[" not in res


def test_color_mos_helper(monkeypatch):
    from utils import cli_theme
    monkeypatch.delenv("NO_COLOR", raising=False)

    s_green = cli_theme.color_mos(45.0)
    s_yellow = cli_theme.color_mos(10.0)
    s_red = cli_theme.color_mos(-15.0)

    assert cli_theme.Color.GREEN in s_green
    assert cli_theme.Color.YELLOW in s_yellow
    assert cli_theme.Color.RED in s_red


def test_color_status_helper(monkeypatch):
    from utils import cli_theme
    monkeypatch.delenv("NO_COLOR", raising=False)

    st_ok = cli_theme.color_status("GREEN")
    st_warn = cli_theme.color_status("ORANGE")
    st_crit = cli_theme.color_status("CRITICAL")

    assert cli_theme.Color.GREEN in st_ok
    assert cli_theme.Color.YELLOW in st_warn
    assert cli_theme.Color.RED in st_crit
