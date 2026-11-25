"""The `openskp` command."""

import sys

import pytest

import openskp


@pytest.mark.parametrize("option", ["-h", "--help", "--version"])
def test_options_are_not_file_names(monkeypatch, capsys, option):
    monkeypatch.setattr(sys, "argv", ["openskp", option])
    openskp.main()
    out, err = capsys.readouterr()
    assert out.startswith("openskp " if option == "--version" else "usage: openskp")
    assert err == ""
