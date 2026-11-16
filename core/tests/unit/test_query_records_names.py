import pytest

from yorath.runtime.tools.query_records import name_tokens


@pytest.mark.parametrize(
    ("name", "tokens"),
    [
        ("Miller", ["miller"]),
        ("MILLER", ["miller"]),
        ("Miller's", ["miller"]),
        ("Miller’s", ["miller"]),
        ("Peter Miller", ["peter", "miller"]),
        ("  Lee ", ["lee"]),
        ("Jones'", ["jones"]),
        ("", []),
    ],
)
def test_name_tokens(name, tokens):
    assert name_tokens(name) == tokens
