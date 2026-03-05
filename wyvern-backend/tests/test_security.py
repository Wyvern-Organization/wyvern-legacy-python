import pytest

from app.utils.security import create_access_token, decode_token


def test_access_token_round_trip() -> None:
    token = create_access_token(123)
    payload = decode_token(token)
    assert payload["sub"] == "123"
    assert payload["type"] == "access"


@pytest.mark.parametrize("user_id", [1, 2, 99])
def test_access_token_subject(user_id: int) -> None:
    token = create_access_token(user_id)
    payload = decode_token(token)
    assert payload["sub"] == str(user_id)
