"""Deterministic demo data: users, games in every state and their players."""

from __future__ import annotations

import random
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from battle_royale import keys
from battle_royale.models import Game, Player, User

if TYPE_CHECKING:
    from collections.abc import Iterator

    from battle_royale.models import Item

MAPS = ("Desierto de la Tatacoa", "Caño Cristales", "Sierra Nevada", "Cañón del Chicamocha")

FIRST_NAMES = (
    "Ana", "Andrés", "Camila", "Carlos", "Daniela", "David", "Diana", "Felipe", "Gabriela",
    "Jorge", "Juliana", "Laura", "Luis", "Manuela", "María", "Mateo", "Natalia", "Santiago",
    "Sofía", "Valentina",
)  # fmt: skip
LAST_NAMES = (
    "Rodríguez", "Gómez", "González", "Martínez", "García", "López", "Hernández", "Sánchez",
    "Ramírez", "Pérez", "Díaz", "Muñoz", "Rojas", "Moreno", "Jiménez", "Vargas", "Castro",
    "Ortiz", "Suárez", "Herrera",
)  # fmt: skip

BASE_TIME = datetime(2021, 1, 15, 17, 0, tzinfo=UTC)
"""Fixed origin so every run produces the same timestamps."""

OPEN_GAMES = 6
FULL_GAMES = 2
RUNNING_GAMES = 4
FINISHED_GAMES = 4


@dataclass(frozen=True, slots=True)
class Dataset:
    users: list[User]
    games: list[Game]
    players: list[Player]

    def items(self) -> Iterator[Item]:
        for user in self.users:
            yield user.to_item()
        for game in self.games:
            yield game.to_item()
        for player in self.players:
            yield player.to_item()


def _ascii(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()


def _users(rng: random.Random, count: int) -> list[User]:
    users: list[User] = []
    taken: set[str] = set()
    while len(users) < count:
        first, last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
        username = f"{_ascii(first)}{_ascii(last)}{rng.randint(1, 99)}"
        if username in taken:
            continue
        taken.add(username)
        users.append(User(username, f"{first} {last}", f"{username}@example.com"))

    return users


def _stamp(moment: datetime) -> str:
    return moment.isoformat(timespec="milliseconds")


def generate(seed: int = 2021, user_count: int = 300) -> Dataset:
    """Builds the same dataset for the same seed.

    Games cover every state: open (1 to 49 players), full but waiting for the creator, running and
    finished with a place for each player.
    """
    rng = random.Random(seed)
    users = _users(rng, user_count)
    usernames = [user.username for user in users]
    states = (
        ["open"] * OPEN_GAMES
        + ["full"] * FULL_GAMES
        + ["running"] * RUNNING_GAMES
        + ["finished"] * FINISHED_GAMES
    )
    games: list[Game] = []
    players: list[Player] = []
    for index, state in enumerate(states):
        game_id = str(uuid.UUID(int=rng.getrandbits(128), version=4))
        people = rng.randint(1, keys.MAX_PLAYERS - 1) if state == "open" else keys.MAX_PLAYERS
        roster = rng.sample(usernames, people)
        created = BASE_TIME + timedelta(hours=index * 7, minutes=rng.randint(0, 59))
        started = created + timedelta(minutes=30) if state in {"running", "finished"} else None
        ended = started + timedelta(minutes=25) if started and state == "finished" else None
        games.append(
            Game(
                game_id=game_id,
                map=rng.choice(MAPS),
                creator=roster[0],
                create_time=_stamp(created),
                people=people,
                open_timestamp=None if started else _stamp(created),
                start_time=_stamp(started) if started else None,
                end_time=_stamp(ended) if ended else None,
            )
        )
        ranking = rng.sample(roster, len(roster)) if ended else []
        places = {username: place for place, username in enumerate(ranking, start=1)}
        players.extend(Player(game_id, username, places.get(username)) for username in roster)

    return Dataset(users, games, players)
