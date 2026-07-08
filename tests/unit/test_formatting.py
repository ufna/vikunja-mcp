"""#85: agent plain text <-> Vikunja HTML comment field."""
from vikunja_mcp.formatting import html_to_text, text_to_html

# markers the workflow greps for; escaping must leave every one of them byte-for-byte
MARKERS = [
    "[claim]", "[spec]", "[worklog]", "[review]", "[нужен человек]",
    "[blocked]", "[decompose]", "[filed-by-agent]", "[review] APPROVE",
    "[review] NEEDS WORK",
]


def test_single_line_is_one_paragraph():
    assert text_to_html("just one line") == "<p>just one line</p>"


def test_single_newline_becomes_br_within_a_paragraph():
    assert text_to_html("line one\nline two") == "<p>line one<br>line two</p>"


def test_blank_line_separates_paragraphs():
    assert text_to_html("para one\n\npara two") == "<p>para one</p><p>para two</p>"


def test_runs_of_blank_lines_collapse_no_empty_paragraphs():
    html = text_to_html("a\n\n\n\nb")
    assert html == "<p>a</p><p>b</p>"
    assert "<p></p>" not in html


def test_crlf_is_normalized():
    assert text_to_html("a\r\nb") == "<p>a<br>b</p>"


def test_empty_and_whitespace_only_yield_empty_string():
    assert text_to_html("") == ""
    assert text_to_html("   \n  \n") == ""


def test_escapes_html_special_chars_so_reports_cannot_corrupt_markup():
    # a report full of code: '<', '&', '>' must be escaped, never emitted as raw tags
    html = text_to_html("if a < b && c > d")
    assert "&lt;" in html and "&amp;" in html and "&gt;" in html
    assert "<b" not in html  # the literal "< b" did NOT become a bogus tag


def test_angle_bracket_placeholder_is_escaped_not_swallowed():
    # a classic agent report token like "<id>" must survive as visible text
    html = text_to_html("pass the <id> here")
    assert "&lt;id&gt;" in html
    assert html_to_text(html) == "pass the <id> here"


def test_markers_survive_and_stay_greppable():
    for marker in MARKERS:
        html = text_to_html(f"{marker}\nbody line")
        # the raw stored HTML still carries the marker literally (ASCII, nothing escaped)
        assert marker in html
        # and after rendering back (what next_task matches on) it leads the text
        assert html_to_text(html).startswith(marker)


def test_worklog_shaped_report_keeps_structure_round_trip():
    raw = "[worklog]\nПричина: X\nСделано: Y\n\nEvidence: commit abc123"
    html = text_to_html(raw)
    # two paragraphs (the blank line before Evidence), line breaks inside the first
    assert html.count("<p>") == 2
    assert "<br>" in html
    back = html_to_text(html)
    assert back.startswith("[worklog]")
    for fragment in ("Причина: X", "Сделано: Y", "Evidence: commit abc123"):
        assert fragment in back
    # the newline structure is preserved for a human/agent reading it
    assert "\n" in back


def test_html_to_text_unescapes_and_strips_tags():
    assert html_to_text("<p>a &lt;b&gt; &amp; c</p>") == "a <b> & c"


def test_html_to_text_is_noop_ish_on_legacy_plain_text():
    # comments written before #85 are stored as bare plain text; reading them must not
    # mangle the leading marker
    assert html_to_text("[review] APPROVE\nlgtm").startswith("[review] APPROVE")


def test_html_to_text_empty_input():
    assert html_to_text("") == ""
    assert html_to_text(None) == ""
