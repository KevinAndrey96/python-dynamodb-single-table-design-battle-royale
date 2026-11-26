"""Access patterns of the battle royale table.

Each public method is one access pattern and maps to a single DynamoDB request (or one request per
page). Rules such as "a game holds at most 50 players" are enforced by condition expressions inside
the same write, so concurrent requests cannot break them. When a condition rejects a write the
repository reads the game once to tell the caller which rule failed.
"""

from __future__ import annotations

import random
import time
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from botocore.exceptions import ClientError

from battle_royale import keys
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
from battle_royale.models import Game, GameWithPlayers, Player, User

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from types_boto3_dynamodb import DynamoDBClient
    from types_boto3_dynamodb.type_defs import TransactWriteItemTypeDef, WriteRequestTypeDef

    from battle_royale.models import Item

BATCH_SIZE = 25
MAX_BATCH_ATTEMPTS = 8
MAX_TRANSACTION_ATTEMPTS = 6


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _code(error: ClientError) -> str:
    return str(error.response.get("Error", {}).get("Code", ""))


def _reasons(error: ClientError) -> list[str]:
    reasons: list[dict[str, Any]] = error.response.get("CancellationReasons", [])  # type: ignore[assignment]

    return [str(reason.get("Code", "None")) for reason in reasons]


def _failed_indexes(error: ClientError) -> list[int]:
    return [i for i, code in enumerate(_reasons(error)) if code == "ConditionalCheckFailed"]


def _is_conflict(error: ClientError) -> bool:
    """A transaction lost a race with another one touching the same item; retrying is safe."""
    codes = _reasons(error)

    return "TransactionConflict" in codes and "ConditionalCheckFailed" not in codes


class BattleRoyaleRepository:
    def __init__(self, client: DynamoDBClient, table_name: str = keys.DEFAULT_TABLE) -> None:
        self._client = client
        self._table = table_name

    # Users

    def create_user(self, user: User) -> None:
        try:
            self._client.put_item(
                TableName=self._table,
                Item=user.to_item(),
                ConditionExpression="attribute_not_exists(PK)",
            )
        except ClientError as error:
            if _code(error) == "ConditionalCheckFailedException":
                raise UserAlreadyExistsError(user.username) from error
            raise

    def get_user(self, username: str) -> User:
        response = self._client.get_item(
            TableName=self._table,
            Key={"PK": {"S": keys.user_pk(username)}, "SK": {"S": keys.user_sk(username)}},
        )
        if "Item" not in response:
            raise UserNotFoundError(username)

        return User.from_item(response["Item"])

    # Games

    def create_game(self, creator: str, map_name: str) -> Game:
        """Creates an open game with its creator as the first player, in one transaction."""
        now = _now()
        game = Game(
            game_id=str(uuid.uuid4()),
            map=map_name,
            creator=creator,
            create_time=now,
            people=1,
            open_timestamp=now,
        )
        try:
            self._transact(
                [
                    self._user_must_exist(creator),
                    {
                        "Put": {
                            "TableName": self._table,
                            "Item": game.to_item(),
                            "ConditionExpression": "attribute_not_exists(PK)",
                        }
                    },
                    {"Put": {"TableName": self._table, "Item": Player(game.game_id, creator).to_item()}},
                ]
            )
        except ClientError as error:
            if _failed_indexes(error) == [0]:
                raise UserNotFoundError(creator) from error
            raise

        return game

    def get_game(self, game_id: str) -> GameWithPlayers:
        """Returns the game and all of its players with one ``Query`` on the game partition."""
        items = self._query_all(
            KeyConditionExpression="PK = :pk AND SK BETWEEN :metadata AND :players",
            ExpressionAttributeValues={
                ":pk": {"S": keys.game_pk(game_id)},
                ":metadata": {"S": keys.game_sk(game_id)},
                ":players": {"S": keys.PLAYERS_UPPER_BOUND},
            },
        )
        if not items or items[0]["SK"]["S"] != keys.game_sk(game_id):
            raise GameNotFoundError(game_id)

        return GameWithPlayers(Game.from_item(items[0]), [Player.from_item(i) for i in items[1:]])

    def find_open_games(self, map_name: str | None = None, limit: int = 10) -> list[Game]:
        """Lists games that still accept players, newest first.

        With a map it queries the sparse index by partition; without one it scans the sparse index,
        which only holds games that have not started, so the scan never touches finished games.
        """
        values: dict[str, Any] = {":limit": {"N": str(keys.MAX_PLAYERS)}}
        if map_name is None:
            pages = self._client.get_paginator("scan").paginate(
                TableName=self._table,
                IndexName=keys.OPEN_GAMES_INDEX,
                FilterExpression="people < :limit",
                ExpressionAttributeValues=values,
            )
            games = [Game.from_item(item) for page in pages for item in page.get("Items", [])]
            games.sort(key=lambda game: game.open_timestamp or "", reverse=True)

            return games[:limit]

        values[":map"] = {"S": map_name}
        pages = self._client.get_paginator("query").paginate(
            TableName=self._table,
            IndexName=keys.OPEN_GAMES_INDEX,
            KeyConditionExpression="#map = :map",
            FilterExpression="people < :limit",
            ExpressionAttributeNames={"#map": "map"},
            ExpressionAttributeValues=values,
            ScanIndexForward=False,
        )
        games = []
        for page in pages:
            games.extend(Game.from_item(item) for item in page.get("Items", []))
            if len(games) >= limit:
                break

        return games[:limit]

    def join_game(self, game_id: str, username: str) -> None:
        """Adds a player and bumps the counter atomically; the 51st request is rejected."""
        try:
            self._transact(
                [
                    self._user_must_exist(username),
                    {
                        "Put": {
                            "TableName": self._table,
                            "Item": Player(game_id, username).to_item(),
                            "ConditionExpression": "attribute_not_exists(SK)",
                        }
                    },
                    {
                        "Update": {
                            "TableName": self._table,
                            "Key": self._game_key(game_id),
                            "UpdateExpression": "SET people = people + :one",
                            "ConditionExpression": "attribute_exists(open_timestamp) AND people < :limit",
                            "ExpressionAttributeValues": {
                                ":one": {"N": "1"},
                                ":limit": {"N": str(keys.MAX_PLAYERS)},
                            },
                        }
                    },
                ]
            )
        except ClientError as error:
            failed = _failed_indexes(error)
            if not failed:
                raise
            if 0 in failed:
                raise UserNotFoundError(username) from error
            if 1 in failed:
                raise AlreadyJoinedError(game_id, username) from error
            game = self._find_game(game_id)
            if game is None:
                raise GameNotFoundError(game_id) from error
            if game.start_time is not None:
                raise GameAlreadyStartedError(game_id) from error
            raise GameFullError(game_id) from error

    def start_game(self, game_id: str, requesting_user: str) -> Game:
        """Starts a full game on behalf of its creator and drops it from the open games index."""
        try:
            response = self._client.update_item(
                TableName=self._table,
                Key=self._game_key(game_id),
                UpdateExpression="SET start_time = :now REMOVE open_timestamp",
                ConditionExpression=(
                    "creator = :user AND people = :limit AND attribute_not_exists(start_time)"
                ),
                ExpressionAttributeValues={
                    ":now": {"S": _now()},
                    ":user": {"S": requesting_user},
                    ":limit": {"N": str(keys.MAX_PLAYERS)},
                },
                ReturnValues="ALL_NEW",
            )
        except ClientError as error:
            if _code(error) != "ConditionalCheckFailedException":
                raise
            game = self._find_game(game_id)
            if game is None:
                raise GameNotFoundError(game_id) from error
            if game.start_time is not None:
                raise GameAlreadyStartedError(game_id) from error
            if game.creator != requesting_user:
                raise NotGameCreatorError(game_id, requesting_user) from error
            raise GameNotFullError(game_id, game.people) from error

        return Game.from_item(response["Attributes"])

    def finish_game(self, game_id: str, ranking: Sequence[str]) -> None:
        """Closes a started game and stores each player's place in the same transaction.

        ``ranking`` lists usernames from the winner down; players left out keep no place.
        """
        if not ranking:
            raise InvalidRankingError("no players")
        if len(set(ranking)) != len(ranking):
            raise InvalidRankingError("repeated players")
        if len(ranking) > keys.MAX_PLAYERS:
            raise InvalidRankingError(f"more than {keys.MAX_PLAYERS} players")

        actions: list[TransactWriteItemTypeDef] = [
            {
                "Update": {
                    "TableName": self._table,
                    "Key": self._game_key(game_id),
                    "UpdateExpression": "SET end_time = :now",
                    "ConditionExpression": "attribute_exists(start_time) AND attribute_not_exists(end_time)",
                    "ExpressionAttributeValues": {":now": {"S": _now()}},
                }
            }
        ]
        actions.extend(
            {
                "Update": {
                    "TableName": self._table,
                    "Key": {"PK": {"S": keys.game_pk(game_id)}, "SK": {"S": keys.player_sk(username)}},
                    "UpdateExpression": "SET place = :place",
                    "ConditionExpression": "attribute_exists(SK)",
                    "ExpressionAttributeValues": {":place": {"N": str(place)}},
                }
            }
            for place, username in enumerate(ranking, start=1)
        )
        try:
            self._transact(actions)
        except ClientError as error:
            failed = _failed_indexes(error)
            if not failed:
                raise
            if failed[0] > 0:
                raise PlayerNotInGameError(game_id, ranking[failed[0] - 1]) from error
            game = self._find_game(game_id)
            if game is None:
                raise GameNotFoundError(game_id) from error
            if game.end_time is not None:
                raise GameAlreadyFinishedError(game_id) from error
            raise GameNotStartedError(game_id) from error

    def games_for_user(self, username: str) -> list[Player]:
        """Lists every game a user joined, through the inverted index."""
        pages = self._client.get_paginator("query").paginate(
            TableName=self._table,
            IndexName=keys.INVERTED_INDEX,
            KeyConditionExpression="SK = :sk AND begins_with(PK, :games)",
            ExpressionAttributeValues={
                ":sk": {"S": keys.player_sk(username)},
                ":games": {"S": keys.GAME_PREFIX},
            },
        )

        return [Player.from_item(item) for page in pages for item in page.get("Items", [])]

    # Bulk load

    def put_all(self, items: Iterable[Item]) -> int:
        """Writes items in batches of 25, retrying unprocessed ones with exponential backoff."""
        batch: list[WriteRequestTypeDef] = []
        written = 0
        for item in items:
            batch.append({"PutRequest": {"Item": item}})
            if len(batch) == BATCH_SIZE:
                written += self._write_batch(batch)
                batch = []
        if batch:
            written += self._write_batch(batch)

        return written

    def _write_batch(self, batch: list[WriteRequestTypeDef]) -> int:
        pending = batch
        for attempt in range(MAX_BATCH_ATTEMPTS):
            response = self._client.batch_write_item(RequestItems={self._table: pending})
            unprocessed = response.get("UnprocessedItems", {}).get(self._table, [])
            pending = cast("list[WriteRequestTypeDef]", unprocessed)
            if not pending:
                return len(batch)
            time.sleep(0.05 * 2**attempt)
        msg = f"batch write: {len(pending)} items left unprocessed"
        raise RuntimeError(msg)

    # Helpers

    def _transact(self, actions: list[TransactWriteItemTypeDef]) -> None:
        attempt = 1
        while True:
            try:
                self._client.transact_write_items(TransactItems=actions)
            except ClientError as error:
                if attempt >= MAX_TRANSACTION_ATTEMPTS or not _is_conflict(error):
                    raise
                time.sleep(random.uniform(0, 0.02 * 2**attempt))
                attempt += 1
            else:
                return

    def _game_key(self, game_id: str) -> dict[str, Any]:
        return {"PK": {"S": keys.game_pk(game_id)}, "SK": {"S": keys.game_sk(game_id)}}

    def _user_must_exist(self, username: str) -> TransactWriteItemTypeDef:
        return {
            "ConditionCheck": {
                "TableName": self._table,
                "Key": {"PK": {"S": keys.user_pk(username)}, "SK": {"S": keys.user_sk(username)}},
                "ConditionExpression": "attribute_exists(PK)",
            }
        }

    def _find_game(self, game_id: str) -> Game | None:
        response = self._client.get_item(TableName=self._table, Key=self._game_key(game_id))

        return Game.from_item(response["Item"]) if "Item" in response else None

    def _query_all(self, **kwargs: Any) -> list[dict[str, Any]]:
        pages = self._client.get_paginator("query").paginate(TableName=self._table, **kwargs)

        return [item for page in pages for item in page.get("Items", [])]
