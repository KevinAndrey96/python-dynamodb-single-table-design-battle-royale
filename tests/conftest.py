from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING

import boto3
import pytest
from moto import mock_aws

from battle_royale import keys, schema
from battle_royale.models import User
from battle_royale.repository import BattleRoyaleRepository

if TYPE_CHECKING:
    from types_boto3_dynamodb import DynamoDBClient


@pytest.fixture(autouse=True)
def _fake_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.delenv("AWS_PROFILE", raising=False)


@pytest.fixture
def bare_client() -> Iterator[DynamoDBClient]:
    """A mocked DynamoDB without tables."""
    with mock_aws():
        yield boto3.client("dynamodb", region_name="us-east-1")


@pytest.fixture
def client(bare_client: DynamoDBClient) -> DynamoDBClient:
    schema.create_table(bare_client)

    return bare_client


@pytest.fixture
def repo(client: DynamoDBClient) -> BattleRoyaleRepository:
    return BattleRoyaleRepository(client)


AddUsers = Callable[[int], list[str]]


@pytest.fixture
def add_users(repo: BattleRoyaleRepository) -> AddUsers:
    def add(count: int) -> list[str]:
        names = [f"jugador{i:02d}" for i in range(count)]
        repo.put_all(User(name, name.title(), f"{name}@example.com").to_item() for name in names)

        return names

    return add


FullGame = Callable[[], tuple[str, list[str]]]


@pytest.fixture
def full_game(repo: BattleRoyaleRepository, add_users: AddUsers) -> FullGame:
    """Creates a game with fifty players and returns its id and roster, creator first."""

    def make() -> tuple[str, list[str]]:
        roster = add_users(keys.MAX_PLAYERS + 1)
        game = repo.create_game(roster[0], "Sierra Nevada")
        for username in roster[1 : keys.MAX_PLAYERS]:
            repo.join_game(game.game_id, username)

        return game.game_id, roster

    return make
