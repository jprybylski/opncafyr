"""Plain-language checks for the report's config.yaml and footnotes file (#64)."""

from __future__ import annotations

import pytest

from opncafyr.adapters import quartifyr as Q
from opncafyr.adapters.quartifyr import config_yaml as C

TEMPLATE = Q.template().joinpath("config.yaml").read_text("utf-8")


def messages(text: str, severity: str | None = None) -> list[str]:
    return [p.message for p in C.check_config(text) if severity in (None, p.severity)]


def test_the_bundled_template_has_no_problems_and_comments_for_a_reader():
    assert C.check_config(TEMPLATE) == []
    assert "Edit report, then Report settings" in TEMPLATE
    assert "# Figures." in TEMPLATE


def test_an_empty_file_keeps_every_default():
    assert C.check_config("") == [] and C.check_config("# only a comment\n") == []


def test_text_that_is_not_yaml_names_the_line():
    (error,) = C.check_config("use_artifact_size: [false\nstrict: true\n")
    assert error.severity == "error"
    assert "not valid YAML" in error.message and "line" in error.message


def test_a_list_of_words_is_not_settings():
    (error,) = C.check_config("- one\n- two\n")
    assert error.severity == "error" and "setting: value" in error.message


@pytest.mark.parametrize("name", ["report_dir_name", "outputs_dir_name"])
def test_the_two_folder_settings_must_stay(name):
    got = messages(f"{name}: elsewhere\n", "error")
    assert len(got) == 1 and name in got[0] and "must stay" in got[0]
    assert C.check_config(f"{name}: {C.PATH_SETTINGS[name]}\n") == []


def test_an_unknown_setting_is_a_warning_about_spelling():
    (warning,) = C.check_config("use_artifact_sizes: false\n")
    assert warning.severity == "warning" and "use_artifact_sizes" in warning.message
    assert "spelling" in warning.message


def test_a_value_of_the_wrong_kind_says_what_it_should_be():
    got = messages('use_artifact_size: "false"\ndefault_fig_width: wide\nfootnote_order: Source\n')
    assert any("use_artifact_size should be true or false, not text" in m for m in got)
    assert any("default_fig_width should be a number, not text" in m for m in got)
    assert any("footnote_order should be a list, not text" in m for m in got)
    assert C.check_config("default_fig_width: 5.5\nuse_artifact_size: false\n") == []


def test_errors_come_before_warnings():
    found = C.check_config("zzz: 1\nreport_dir_name: x\n")
    assert [p.severity for p in found] == ["error", "warning"]


def test_the_footnotes_file_must_parse_and_hold_named_entries():
    assert C.check_footnotes("") == []
    assert C.check_footnotes("a:\n  b: c\n") == []
    assert C.check_footnotes("a: [\n")[0].severity == "error"
    assert "named entries" in C.check_footnotes("- x\n")[0].message
