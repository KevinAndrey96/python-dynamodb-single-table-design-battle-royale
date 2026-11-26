"""Runs against a real DynamoDB Local; skipped unless ``DYNAMODB_ENDPOINT_URL`` is set."""

from __future__ import annotations

import os
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

import pytest

from battle_royale import cli, keys, schema
from battle_royale.errors import GameFullError
from battle_royale.models import User
from battle_royale.repository import BattleRoyaleRepository

if TYPE_CHECKING:
    from collections.abc import Iterator

    from types_boto3_dynamodb import DynamoDBClient

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("DYNAMODB_ENDPOINT_URL"), reason="DYNAMODB_ENDPOINT_URL is not set"
    ),
]


@pytest.fixture
def local(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[DynamoDBClient, str]]:
    monkeypatch.setenv("DYNAMODB_ENDPOINT_URL", os.environ["DYNAMODB_ENDPOINT_URL"])
    client = cli.make_client()
    table = f"battle-royale-{uuid.uuid4().hex[:8]}"
    schema.create_table(client, table)
    yield client, table
    schema.delete_table(client, table)


def test_demo_against_dynamodb_local(
    local: tuple[DynamoDBClient, str], capsys: pytest.CaptureFixture[str]
) -> None:
    client, table = local

    assert cli.main(["--tabla", table, "cargar-datos"], client) == 0
    assert cli.main(["--tabla", table, "demo"], client) == 0
    assert "sale del índice de partidas abiertas" in capsys.readouterr().out


def test_concurrent_joins_never_exceed_the_limit(local: tuple[DynamoDBClient, str]) -> None:
    client, table = local
    repo = BattleRoyaleRepository(client, table)
    names = [f"rival{i:02d}" for i in range(keys.MAX_PLAYERS + 20)]
    repo.put_all(User(name, name, f"{name}@example.com").to_item() for name in names)
    game = repo.create_game(names[0], "Sierra Nevada")

    def join(username: str) -> str:
        try:
            repo.join_game(game.game_id, username)
        except GameFullError:
            return "full"

        return "joined"

    with ThreadPoolExecutor(max_workers=16) as pool:
        outcomes = Counter(pool.map(join, names[1:]))

    details = repo.get_game(game.game_id)
    assert outcomes == {"joined": keys.MAX_PLAYERS - 1, "full": 20}
    assert details.game.people == keys.MAX_PLAYERS
    assert len(details.players) == keys.MAX_PLAYERS
