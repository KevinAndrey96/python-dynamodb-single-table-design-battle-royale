"""Entities and their mapping to DynamoDB attribute values."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from battle_royale import keys

if TYPE_CHECKING:
    from types_boto3_dynamodb.type_defs import AttributeValueTypeDef

    Item = dict[str, AttributeValueTypeDef]

StoredItem = Mapping[str, Any]
"""An item as the low level client returns it, such as ``{"name": {"S": "Ana"}}``."""


def _s(item: StoredItem, name: str) -> str:
    return str(item[name]["S"])


def _opt_s(item: StoredItem, name: str) -> str | None:
    value = item.get(name)

    return None if value is None else str(value["S"])


def _n(item: StoredItem, name: str) -> int:
    return int(item[name]["N"])


def _opt_n(item: StoredItem, name: str) -> int | None:
    value = item.get(name)

    return None if value is None else int(value["N"])


@dataclass(frozen=True, slots=True)
class User:
    username: str
    name: str
    email: str

    def to_item(self) -> Item:
        return {
            "PK": {"S": keys.user_pk(self.username)},
            "SK": {"S": keys.user_sk(self.username)},
            "username": {"S": self.username},
            "name": {"S": self.name},
            "email": {"S": self.email},
        }

    @classmethod
    def from_item(cls, item: StoredItem) -> User:
        return cls(username=_s(item, "username"), name=_s(item, "name"), email=_s(item, "email"))


@dataclass(frozen=True, slots=True)
class Game:
    game_id: str
    map: str
    creator: str
    create_time: str
    people: int
    open_timestamp: str | None = None
    start_time: str | None = None
    end_time: str | None = None

    @property
    def status(self) -> str:
        if self.end_time is not None:
            return "terminada"
        if self.start_time is not None:
            return "en curso"
        if self.people >= keys.MAX_PLAYERS:
            return "llena"

        return "abierta"

    def to_item(self) -> Item:
        item: Item = {
            "PK": {"S": keys.game_pk(self.game_id)},
            "SK": {"S": keys.game_sk(self.game_id)},
            "game_id": {"S": self.game_id},
            "map": {"S": self.map},
            "creator": {"S": self.creator},
            "create_time": {"S": self.create_time},
            "people": {"N": str(self.people)},
        }
        for name in ("open_timestamp", "start_time", "end_time"):
            value = getattr(self, name)
            if value is not None:
                item[name] = {"S": value}

        return item

    @classmethod
    def from_item(cls, item: StoredItem) -> Game:
        return cls(
            game_id=_s(item, "game_id"),
            map=_s(item, "map"),
            creator=_s(item, "creator"),
            create_time=_s(item, "create_time"),
            people=_n(item, "people"),
            open_timestamp=_opt_s(item, "open_timestamp"),
            start_time=_opt_s(item, "start_time"),
            end_time=_opt_s(item, "end_time"),
        )


@dataclass(frozen=True, slots=True)
class Player:
    """A user inside a game; ``place`` is set when the game finishes (1 is the winner)."""

    game_id: str
    username: str
    place: int | None = None

    def to_item(self) -> Item:
        item: Item = {
            "PK": {"S": keys.game_pk(self.game_id)},
            "SK": {"S": keys.player_sk(self.username)},
            "game_id": {"S": self.game_id},
            "username": {"S": self.username},
        }
        if self.place is not None:
            item["place"] = {"N": str(self.place)}

        return item

    @classmethod
    def from_item(cls, item: StoredItem) -> Player:
        return cls(
            game_id=_s(item, "game_id"),
            username=_s(item, "username"),
            place=_opt_n(item, "place"),
        )


@dataclass(frozen=True, slots=True)
class GameWithPlayers:
    game: Game
    players: list[Player] = field(default_factory=list)
