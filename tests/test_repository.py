from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError

from battle_royale import keys, repository
from battle_royale.errors import (
    AlreadyJoinedError,
    GameAlreadyFinishedError,
    GameAlreadyStartedError,
    GameFullError,
    GameNotFoundError,
    GameNotFullError,
    GameNotStartedError,
    InvalidRankingError,
    NotGameCreatorError,
    PlayerNotInGameError,
    UserAlreadyExistsError,
    UserNotFoundError,
)
from battle_royale.models import Player, User
from battle_royale.repository import BattleRoyaleRepository

if TYPE_CHECKING:
    from types_boto3_dynamodb import DynamoDBClient

    from tests.conftest import AddUsers, FullGame

MISSING = "00000000-0000-4000-8000-000000000000"


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("battle_royale.repository.time.sleep", lambda _seconds: None)


def _cancelled(*codes: str) -> ClientError:
    response: Any = {
        "Error": {"Code": "TransactionCanceledException", "Message": "cancelled"},
        "CancellationReasons": [{"Code": code} for code in codes],
    }

    return ClientError(response, "TransactWriteItems")


class TestUsers:
    def test_round_trip(self, repo: BattleRoyaleRepository) -> None:
        user = User("anagomez7", "Ana Gómez", "anagomez7@example.com")
        repo.create_user(user)

        assert repo.get_user("anagomez7") == user

    def test_usernames_are_unique(self, repo: BattleRoyaleRepository) -> None:
        repo.create_user(User("ana", "Ana", "ana@example.com"))

        with pytest.raises(UserAlreadyExistsError, match="user ana already exists"):
            repo.create_user(User("ana", "Otra Ana", "otra@example.com"))
        assert repo.get_user("ana").name == "Ana"

    def test_missing_user(self, repo: BattleRoyaleRepository) -> None:
        with pytest.raises(UserNotFoundError):
            repo.get_user("nadie")


class TestCreateAndReadGames:
    def test_creator_is_the_first_player(self, repo: BattleRoyaleRepository, add_users: AddUsers) -> None:
        [creator] = add_users(1)
        game = repo.create_game(creator, "Caño Cristales")

        details = repo.get_game(game.game_id)
        assert details.game == game
        assert details.game.people == 1
        assert details.game.status == "abierta"
        assert details.game.open_timestamp == details.game.create_time
        assert details.players == [Player(game.game_id, creator)]

    def test_creator_must_exist(self, repo: BattleRoyaleRepository) -> None:
        with pytest.raises(UserNotFoundError):
            repo.create_game("nadie", "Caño Cristales")
        assert repo.find_open_games() == []

    def test_one_query_returns_the_game_and_every_player(
        self, repo: BattleRoyaleRepository, full_game: FullGame
    ) -> None:
        game_id, roster = full_game()

        details = repo.get_game(game_id)
        assert details.game.people == keys.MAX_PLAYERS
        assert sorted(p.username for p in details.players) == sorted(roster[: keys.MAX_PLAYERS])

    def test_missing_game(self, repo: BattleRoyaleRepository) -> None:
        with pytest.raises(GameNotFoundError, match=f"game {MISSING} not found"):
            repo.get_game(MISSING)

    def test_players_without_metadata_are_not_a_game(self, repo: BattleRoyaleRepository) -> None:
        repo.put_all([Player(MISSING, "huerfano").to_item()])

        with pytest.raises(GameNotFoundError):
            repo.get_game(MISSING)


class TestJoinGame:
    def test_join_adds_the_player_and_counts_it(
        self, repo: BattleRoyaleRepository, add_users: AddUsers
    ) -> None:
        creator, guest = add_users(2)
        game = repo.create_game(creator, "Sierra Nevada")

        repo.join_game(game.game_id, guest)
        details = repo.get_game(game.game_id)
        assert details.game.people == 2
        assert {p.username for p in details.players} == {creator, guest}

    def test_joining_twice_changes_nothing(self, repo: BattleRoyaleRepository, add_users: AddUsers) -> None:
        creator, guest = add_users(2)
        game = repo.create_game(creator, "Sierra Nevada")
        repo.join_game(game.game_id, guest)

        with pytest.raises(AlreadyJoinedError):
            repo.join_game(game.game_id, guest)
        assert repo.get_game(game.game_id).game.people == 2

    def test_unknown_user(self, repo: BattleRoyaleRepository, add_users: AddUsers) -> None:
        [creator] = add_users(1)
        game = repo.create_game(creator, "Sierra Nevada")

        with pytest.raises(UserNotFoundError):
            repo.join_game(game.game_id, "nadie")
        assert repo.get_game(game.game_id).game.people == 1

    def test_unknown_game(self, repo: BattleRoyaleRepository, add_users: AddUsers) -> None:
        [guest] = add_users(1)

        with pytest.raises(GameNotFoundError):
            repo.join_game(MISSING, guest)
        assert repo.games_for_user(guest) == []

    def test_the_fifty_first_player_is_rejected(
        self, repo: BattleRoyaleRepository, full_game: FullGame
    ) -> None:
        game_id, roster = full_game()

        with pytest.raises(GameFullError, match="is full"):
            repo.join_game(game_id, roster[keys.MAX_PLAYERS])
        details = repo.get_game(game_id)
        assert details.game.people == keys.MAX_PLAYERS
        assert len(details.players) == keys.MAX_PLAYERS
        assert details.game.status == "llena"

    def test_started_games_take_no_players(self, repo: BattleRoyaleRepository, full_game: FullGame) -> None:
        game_id, roster = full_game()
        repo.start_game(game_id, roster[0])

        with pytest.raises(GameAlreadyStartedError):
            repo.join_game(game_id, roster[keys.MAX_PLAYERS])

    def test_transaction_conflicts_are_retried(self, client: DynamoDBClient, add_users: AddUsers) -> None:
        creator, guest = add_users(2)
        repo = BattleRoyaleRepository(client)
        game = repo.create_game(creator, "Sierra Nevada")
        conflicts = iter([_cancelled("None", "None", "TransactionConflict")])

        def lose_one_race(**kwargs: Any) -> Any:
            conflict = next(conflicts, None)
            if conflict is not None:
                raise conflict

            return client.transact_write_items(**kwargs)

        flaky = Mock(wraps=client)
        flaky.transact_write_items.side_effect = lose_one_race

        BattleRoyaleRepository(flaky).join_game(game.game_id, guest)
        assert flaky.transact_write_items.call_count == 2
        assert repo.get_game(game.game_id).game.people == 2

    def test_persistent_conflicts_surface(self, client: DynamoDBClient, add_users: AddUsers) -> None:
        creator, guest = add_users(2)
        game = BattleRoyaleRepository(client).create_game(creator, "Sierra Nevada")
        flaky = Mock(wraps=client)
        flaky.transact_write_items.side_effect = _cancelled("None", "None", "TransactionConflict")

        with pytest.raises(ClientError):
            BattleRoyaleRepository(flaky).join_game(game.game_id, guest)
        assert flaky.transact_write_items.call_count == repository.MAX_TRANSACTION_ATTEMPTS


class TestStartGame:
    def test_creator_starts_a_full_game(self, repo: BattleRoyaleRepository, full_game: FullGame) -> None:
        game_id, roster = full_game()

        started = repo.start_game(game_id, roster[0])
        assert started.start_time is not None
        assert started.open_timestamp is None
        assert started.status == "en curso"
        assert game_id not in {g.game_id for g in repo.find_open_games()}

    def test_only_full_games_start(self, repo: BattleRoyaleRepository, add_users: AddUsers) -> None:
        [creator] = add_users(1)
        game = repo.create_game(creator, "Sierra Nevada")

        with pytest.raises(GameNotFullError, match="has 1 of the players") as caught:
            repo.start_game(game.game_id, creator)
        assert caught.value.people == 1

    def test_only_the_creator_starts(self, repo: BattleRoyaleRepository, full_game: FullGame) -> None:
        game_id, roster = full_game()

        with pytest.raises(NotGameCreatorError):
            repo.start_game(game_id, roster[1])

    def test_a_game_starts_once(self, repo: BattleRoyaleRepository, full_game: FullGame) -> None:
        game_id, roster = full_game()
        repo.start_game(game_id, roster[0])

        with pytest.raises(GameAlreadyStartedError):
            repo.start_game(game_id, roster[0])

    def test_missing_game(self, repo: BattleRoyaleRepository) -> None:
        with pytest.raises(GameNotFoundError):
            repo.start_game(MISSING, "nadie")


class TestFinishGame:
    def test_places_are_stored_with_the_end_time(
        self, repo: BattleRoyaleRepository, full_game: FullGame
    ) -> None:
        game_id, roster = full_game()
        repo.start_game(game_id, roster[0])
        ranking = list(reversed(roster[: keys.MAX_PLAYERS]))

        repo.finish_game(game_id, ranking)
        details = repo.get_game(game_id)
        assert details.game.status == "terminada"
        places = {p.username: p.place for p in details.players}
        assert places == {username: place for place, username in enumerate(ranking, start=1)}

    def test_partial_rankings_leave_the_rest_without_place(
        self, repo: BattleRoyaleRepository, full_game: FullGame
    ) -> None:
        game_id, roster = full_game()
        repo.start_game(game_id, roster[0])

        repo.finish_game(game_id, roster[:3])
        places = {p.username: p.place for p in repo.get_game(game_id).players}
        assert [places[name] for name in roster[:4]] == [1, 2, 3, None]

    @pytest.mark.parametrize(
        ("ranking", "reason"),
        [
            ([], "no players"),
            (["ana", "ana"], "repeated players"),
            ([f"j{i}" for i in range(keys.MAX_PLAYERS + 1)], "more than 50 players"),
        ],
    )
    def test_rankings_are_validated(
        self, repo: BattleRoyaleRepository, ranking: list[str], reason: str
    ) -> None:
        with pytest.raises(InvalidRankingError, match=reason):
            repo.finish_game(MISSING, ranking)

    def test_games_that_did_not_start(self, repo: BattleRoyaleRepository, full_game: FullGame) -> None:
        game_id, roster = full_game()

        with pytest.raises(GameNotStartedError):
            repo.finish_game(game_id, roster[:1])

    def test_every_ranked_user_must_be_playing(
        self, repo: BattleRoyaleRepository, full_game: FullGame
    ) -> None:
        game_id, roster = full_game()
        repo.start_game(game_id, roster[0])

        with pytest.raises(PlayerNotInGameError, match=f"user {roster[keys.MAX_PLAYERS]} is not playing"):
            repo.finish_game(game_id, [roster[0], roster[keys.MAX_PLAYERS]])
        assert repo.get_game(game_id).game.end_time is None

    def test_a_game_finishes_once(self, repo: BattleRoyaleRepository, full_game: FullGame) -> None:
        game_id, roster = full_game()
        repo.start_game(game_id, roster[0])
        repo.finish_game(game_id, roster[:1])

        with pytest.raises(GameAlreadyFinishedError):
            repo.finish_game(game_id, roster[:1])

    def test_missing_game(self, repo: BattleRoyaleRepository) -> None:
        with pytest.raises(GameNotFoundError):
            repo.finish_game(MISSING, ["ana"])


class TestQueries:
    def test_open_games_by_map_newest_first_without_full_ones(
        self, repo: BattleRoyaleRepository, add_users: AddUsers, full_game: FullGame
    ) -> None:
        full_id, _ = full_game()
        creators = add_users(3)
        older = repo.create_game(creators[0], "Sierra Nevada")
        newer = repo.create_game(creators[1], "Sierra Nevada")
        repo.create_game(creators[2], "Caño Cristales")

        found = repo.find_open_games("Sierra Nevada")
        assert [g.game_id for g in found] == [newer.game_id, older.game_id]
        assert full_id not in {g.game_id for g in found}
        assert [g.game_id for g in repo.find_open_games("Sierra Nevada", limit=1)] == [newer.game_id]

    def test_open_games_on_every_map(self, repo: BattleRoyaleRepository, add_users: AddUsers) -> None:
        creators = add_users(3)
        games = [repo.create_game(c, m) for c, m in zip(creators, ["A", "B", "C"], strict=True)]

        assert [g.game_id for g in repo.find_open_games()] == [g.game_id for g in reversed(games)]
        assert len(repo.find_open_games(limit=2)) == 2

    def test_games_for_user_through_the_inverted_index(
        self, repo: BattleRoyaleRepository, full_game: FullGame
    ) -> None:
        game_id, roster = full_game()
        repo.start_game(game_id, roster[0])
        repo.finish_game(game_id, [roster[5]])
        other = repo.create_game(roster[5], "Caño Cristales")

        history = {p.game_id: p.place for p in repo.games_for_user(roster[5])}
        assert history == {game_id: 1, other.game_id: None}
        assert repo.games_for_user("nadie") == []


class TestBulkLoad:
    def test_writes_in_batches(self, repo: BattleRoyaleRepository) -> None:
        users = [User(f"u{i}", "U", "u@example.com") for i in range(60)]

        assert repo.put_all(u.to_item() for u in users) == 60
        assert repo.put_all(u.to_item() for u in users[:50]) == 50
        assert repo.get_user("u59").username == "u59"

    def test_unprocessed_items_are_retried(self, client: DynamoDBClient) -> None:
        item = User("ana", "Ana", "ana@example.com").to_item()
        flaky = Mock(wraps=client)
        flaky.batch_write_item.side_effect = [
            {"UnprocessedItems": {keys.DEFAULT_TABLE: [{"PutRequest": {"Item": item}}]}},
            {"UnprocessedItems": {}},
        ]

        assert BattleRoyaleRepository(flaky).put_all([item]) == 1
        assert flaky.batch_write_item.call_count == 2

    def test_gives_up_after_the_last_attempt(self, client: DynamoDBClient) -> None:
        item = User("ana", "Ana", "ana@example.com").to_item()
        flaky = Mock(wraps=client)
        flaky.batch_write_item.return_value = {
            "UnprocessedItems": {keys.DEFAULT_TABLE: [{"PutRequest": {"Item": item}}]}
        }

        with pytest.raises(RuntimeError, match="batch write: 1 items left unprocessed"):
            BattleRoyaleRepository(flaky).put_all([item])
        assert flaky.batch_write_item.call_count == repository.MAX_BATCH_ATTEMPTS


class TestInfrastructureErrors:
    """Errors that are not rule violations reach the caller untouched."""

    @pytest.fixture
    def broken(self, client: DynamoDBClient) -> BattleRoyaleRepository:
        return BattleRoyaleRepository(client, "no-existe")

    def test_create_user(self, broken: BattleRoyaleRepository) -> None:
        with pytest.raises(ClientError):
            broken.create_user(User("ana", "Ana", "ana@example.com"))

    def test_create_game(self, broken: BattleRoyaleRepository) -> None:
        with pytest.raises(ClientError):
            broken.create_game("ana", "A")

    def test_join_game(self, broken: BattleRoyaleRepository) -> None:
        with pytest.raises(ClientError):
            broken.join_game(MISSING, "ana")

    def test_start_game(self, broken: BattleRoyaleRepository) -> None:
        with pytest.raises(ClientError):
            broken.start_game(MISSING, "ana")

    def test_finish_game(self, broken: BattleRoyaleRepository) -> None:
        with pytest.raises(ClientError):
            broken.finish_game(MISSING, ["ana"])
