from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, Any
from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError

from battle_royale import keys, schema, seed
from battle_royale.models import Game, Player, User

if TYPE_CHECKING:
    from types_boto3_dynamodb import DynamoDBClient


class TestKeys:
    def test_players_sort_between_the_metadata_and_the_upper_bound(self) -> None:
        game = "g1"
        players = [keys.player_sk(name) for name in ("ana", "zoe", "~")]

        assert all(keys.game_sk(game) < sk < keys.PLAYERS_UPPER_BOUND for sk in players)

    def test_prefixes(self) -> None:
        assert keys.user_pk("ana") == "USER#ana"
        assert keys.user_sk("ana") == "#METADATA#ana"
        assert keys.game_pk("g1") == "GAME#g1"
        assert keys.game_sk("g1") == "#METADATA#g1"
        assert keys.player_sk("ana") == "USER#ana"


class TestModels:
    @pytest.mark.parametrize(
        "game",
        [
            Game("g1", "A", "ana", "t0", 3, open_timestamp="t0"),
            Game("g1", "A", "ana", "t0", 50, start_time="t1"),
            Game("g1", "A", "ana", "t0", 50, start_time="t1", end_time="t2"),
        ],
    )
    def test_games_round_trip(self, game: Game) -> None:
        assert Game.from_item(game.to_item()) == game

    def test_optional_attributes_are_not_written(self) -> None:
        item = Game("g1", "A", "ana", "t0", 3, open_timestamp="t0").to_item()

        assert "start_time" not in item
        assert "end_time" not in item
        assert "place" not in Player("g1", "ana").to_item()

    @pytest.mark.parametrize(
        ("game", "status"),
        [
            (Game("g", "A", "a", "t", 10, open_timestamp="t"), "abierta"),
            (Game("g", "A", "a", "t", 50, open_timestamp="t"), "llena"),
            (Game("g", "A", "a", "t", 50, start_time="t"), "en curso"),
            (Game("g", "A", "a", "t", 50, start_time="t", end_time="t"), "terminada"),
        ],
    )
    def test_status(self, game: Game, status: str) -> None:
        assert game.status == status

    def test_users_and_players_round_trip(self) -> None:
        user = User("ana", "Ana Gómez", "ana@example.com")
        player = Player("g1", "ana", 3)

        assert User.from_item(user.to_item()) == user
        assert Player.from_item(player.to_item()) == player


class TestSeed:
    def test_is_deterministic(self) -> None:
        assert seed.generate(7) == seed.generate(7)
        assert seed.generate(7) != seed.generate(8)

    def test_covers_every_game_state(self) -> None:
        data = seed.generate()

        assert Counter(g.status for g in data.games) == {
            "abierta": seed.OPEN_GAMES,
            "llena": seed.FULL_GAMES,
            "en curso": seed.RUNNING_GAMES,
            "terminada": seed.FINISHED_GAMES,
        }
        assert {g.map for g in data.games} <= set(seed.MAPS)

    def test_is_consistent(self) -> None:
        data = seed.generate()
        usernames = {u.username for u in data.users}

        assert len(usernames) == len(data.users) == 300
        assert all(u.username.isascii() and u.email.endswith("@example.com") for u in data.users)
        for game in data.games:
            roster = [p for p in data.players if p.game_id == game.game_id]
            assert len(roster) == game.people
            assert {p.username for p in roster} <= usernames
            assert game.creator in {p.username for p in roster}
            assert (game.open_timestamp is None) == (game.start_time is not None)
            places = sorted(p.place for p in roster if p.place is not None)
            assert places == (list(range(1, game.people + 1)) if game.end_time else [])

    def test_items_cover_every_entity(self) -> None:
        data = seed.generate(user_count=60)

        assert len(list(data.items())) == len(data.users) + len(data.games) + len(data.players)


class TestSchema:
    def test_create_is_idempotent(self, bare_client: DynamoDBClient) -> None:
        assert schema.create_table(bare_client) is True
        assert schema.create_table(bare_client) is False

        table = bare_client.describe_table(TableName=keys.DEFAULT_TABLE)["Table"]
        indexes = {i["IndexName"] for i in table.get("GlobalSecondaryIndexes", [])}
        assert indexes == {keys.OPEN_GAMES_INDEX, keys.INVERTED_INDEX}
        assert table.get("BillingModeSummary", {}).get("BillingMode") == "PAY_PER_REQUEST"

    def test_delete_is_idempotent(self, bare_client: DynamoDBClient) -> None:
        schema.create_table(bare_client)

        assert schema.delete_table(bare_client) is True
        assert schema.delete_table(bare_client) is False

    def test_other_errors_surface(self) -> None:
        denied: Any = {"Error": {"Code": "AccessDeniedException", "Message": "denied"}}
        client = Mock()
        client.create_table.side_effect = ClientError(denied, "CreateTable")
        client.delete_table.side_effect = ClientError(denied, "DeleteTable")

        with pytest.raises(ClientError, match="AccessDenied"):
            schema.create_table(client)
        with pytest.raises(ClientError, match="AccessDenied"):
            schema.delete_table(client)
