import pytest

from cloud_cleanup import is_protected


@pytest.mark.parametrize("tags, expected", [
    ({"do-not-delete": "true"}, True),
    ({"do-not-delete": "TRUE "}, True),
    ({"do-not-delete": "yes"}, True),
    ({"do-not-delete": "false"}, False),
    ({"Owner": "data-team"}, False),
    ({}, False),
])
def test_do_not_delete_tag(tags, expected):
    assert is_protected(tags) is expected