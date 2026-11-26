"""Key layout of the single table.

Every entity shares the generic ``PK`` and ``SK`` attributes. A game and its players live in the
same partition so one ``Query`` returns both; two global secondary indexes add the remaining access
patterns without a second table.

==================  ======================  ======================
Entity              PK                      SK
==================  ======================  ======================
User                ``USER#<username>``     ``#METADATA#<username>``
Game                ``GAME#<game_id>``      ``#METADATA#<game_id>``
Player in a game    ``GAME#<game_id>``      ``USER#<username>``
==================  ======================  ======================
"""

from typing import Final

DEFAULT_TABLE: Final = "battle-royale"
MAX_PLAYERS: Final = 50

OPEN_GAMES_INDEX: Final = "OpenGamesIndex"
"""Sparse index on ``map`` and ``open_timestamp``: only games that have not started carry it."""

INVERTED_INDEX: Final = "InvertedIndex"
"""Swaps ``PK`` and ``SK`` so the games of a user become a single ``Query``."""

USER_PREFIX: Final = "USER#"
GAME_PREFIX: Final = "GAME#"
METADATA_PREFIX: Final = "#METADATA#"

PLAYERS_UPPER_BOUND: Final = "USER$"
"""Sorts right after every ``USER#...`` key because ``$`` (0x24) follows ``#`` (0x23)."""


def user_pk(username: str) -> str:
    return USER_PREFIX + username


def user_sk(username: str) -> str:
    return METADATA_PREFIX + username


def game_pk(game_id: str) -> str:
    return GAME_PREFIX + game_id


def game_sk(game_id: str) -> str:
    return METADATA_PREFIX + game_id


def player_sk(username: str) -> str:
    return USER_PREFIX + username
