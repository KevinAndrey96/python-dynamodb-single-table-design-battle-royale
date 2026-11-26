"""Command line interface in Spanish over the repository."""

from __future__ import annotations

import argparse
import os
import sys
from typing import TYPE_CHECKING

import boto3

from battle_royale import keys, schema, seed
from battle_royale.errors import BattleRoyaleError, GameFullError, NotGameCreatorError
from battle_royale.models import Game, GameWithPlayers, User
from battle_royale.repository import BattleRoyaleRepository

if TYPE_CHECKING:
    from collections.abc import Sequence

    from types_boto3_dynamodb import DynamoDBClient

    from battle_royale.models import Player

EXIT_DOMAIN_ERROR = 1


def make_client() -> DynamoDBClient:
    """Builds the client from the environment; ``DYNAMODB_ENDPOINT_URL`` points to DynamoDB Local."""
    return boto3.client(
        "dynamodb",
        endpoint_url=os.environ.get("DYNAMODB_ENDPOINT_URL") or None,
        region_name=os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1",
    )


def _game_line(game: Game) -> str:
    seats = f"{game.people:>2}/{keys.MAX_PLAYERS}"

    return f"{game.game_id}  {game.map:<24} {seats}  {game.status:<9} creador {game.creator}"


def _print_game(details: GameWithPlayers) -> None:
    game = details.game
    print(f"Partida {game.game_id}")
    print(f"  Mapa     : {game.map}")
    print(f"  Creador  : {game.creator}")
    print(f"  Estado   : {game.status} ({game.people}/{keys.MAX_PLAYERS} jugadores)")
    print(f"  Creada   : {game.create_time}")
    if game.start_time:
        print(f"  Iniciada : {game.start_time}")
    if game.end_time:
        print(f"  Terminada: {game.end_time}")
    ordered = sorted(details.players, key=lambda p: (p.place is None, p.place or 0, p.username))
    for player in ordered:
        place = f"{player.place:>2}." if player.place else "  -"
        print(f"  {place} {player.username}")


def _print_history(username: str, games: list[Player]) -> None:
    print(f"Partidas de {username}: {len(games)}")
    for player in games:
        place = f"puesto {player.place}" if player.place else "sin puesto"
        print(f"  {player.game_id}  {place}")


def _podium(details: GameWithPlayers, count: int) -> GameWithPlayers:
    ranked = sorted((p for p in details.players if p.place), key=lambda p: p.place or 0)

    return GameWithPlayers(details.game, ranked[:count])


def _seed(client: DynamoDBClient, table: str, seed_value: int) -> int:
    dataset = seed.generate(seed_value)

    return BattleRoyaleRepository(client, table).put_all(dataset.items())


def _demo(client: DynamoDBClient, table: str) -> None:
    if schema.create_table(client, table):
        print(f"Tabla {table} creada; cargados {_seed(client, table, 2021)} ítems de demostración.")
    repo = BattleRoyaleRepository(client, table)
    dataset = seed.generate()

    map_name = seed.MAPS[0]
    print(f"\nPartidas abiertas en {map_name}:")
    for game in repo.find_open_games(map_name, limit=5):
        print("  " + _game_line(game))

    creator, *others = [user.username for user in dataset.users[: keys.MAX_PLAYERS + 1]]
    game = repo.create_game(creator, map_name)
    print(f"\n{creator} crea la partida {game.game_id} en {map_name}.")
    for username in others[: keys.MAX_PLAYERS - 1]:
        repo.join_game(game.game_id, username)
    print(f"Se unen {keys.MAX_PLAYERS - 1} jugadores más; la partida queda llena.")
    try:
        repo.join_game(game.game_id, others[-1])
    except GameFullError as error:
        print(f"{others[-1]} intenta unirse y la escritura condicional lo rechaza: {error}.")
    try:
        repo.start_game(game.game_id, others[0])
    except NotGameCreatorError as error:
        print(f"{others[0]} intenta iniciarla: {error}.")
    started = repo.start_game(game.game_id, creator)
    print(f"{creator} la inicia a las {started.start_time} y sale del índice de partidas abiertas.")

    ranking = [creator, *others[: keys.MAX_PLAYERS - 1]]
    ranking.reverse()
    repo.finish_game(game.game_id, ranking)
    print(f"Termina la partida; gana {ranking[0]}.\n")
    details = repo.get_game(game.game_id)
    _print_game(_podium(details, 3))
    print()
    _print_history(ranking[0], repo.games_for_user(ranking[0]))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="battle-royale",
        description="Modelado de una sola tabla en DynamoDB para un juego battle royale.",
    )
    parser.add_argument(
        "--tabla",
        default=os.environ.get("BATTLE_ROYALE_TABLE", keys.DEFAULT_TABLE),
        help="nombre de la tabla (por defecto battle-royale o BATTLE_ROYALE_TABLE)",
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="comando")

    commands.add_parser("crear-tabla", help="crea la tabla y sus dos índices")
    commands.add_parser("borrar-tabla", help="borra la tabla")
    load = commands.add_parser("cargar-datos", help="carga usuarios y partidas de demostración")
    load.add_argument("--semilla", type=int, default=2021, help="semilla del generador")

    user = commands.add_parser("crear-usuario", help="registra un usuario")
    user.add_argument("usuario")
    user.add_argument("--nombre", required=True)
    user.add_argument("--email", required=True)

    show_user = commands.add_parser("usuario", help="muestra el perfil de un usuario")
    show_user.add_argument("usuario")

    create = commands.add_parser("crear-partida", help="crea una partida abierta")
    create.add_argument("creador")
    create.add_argument("mapa")

    open_games = commands.add_parser("abiertas", help="lista partidas que aceptan jugadores")
    open_games.add_argument("--mapa", help="filtra por mapa con una consulta al índice")
    open_games.add_argument("--limite", type=int, default=10)

    show = commands.add_parser("partida", help="muestra una partida y sus jugadores")
    show.add_argument("partida")

    join = commands.add_parser("unirse", help="une un usuario a una partida")
    join.add_argument("partida")
    join.add_argument("usuario")

    start = commands.add_parser("iniciar", help="inicia una partida llena")
    start.add_argument("partida")
    start.add_argument("creador")

    finish = commands.add_parser("terminar", help="cierra la partida con el ranking del ganador hacia abajo")
    finish.add_argument("partida")
    finish.add_argument("ranking", nargs="+", metavar="usuario")

    history = commands.add_parser("historial", help="lista las partidas de un usuario")
    history.add_argument("usuario")

    commands.add_parser("demo", help="recorre todos los patrones de acceso de principio a fin")

    return parser


def run(args: argparse.Namespace, client: DynamoDBClient) -> None:
    table: str = args.tabla
    repo = BattleRoyaleRepository(client, table)
    match args.command:
        case "crear-tabla":
            created = schema.create_table(client, table)
            print(f"Tabla {table} creada." if created else f"La tabla {table} ya existía.")
        case "borrar-tabla":
            deleted = schema.delete_table(client, table)
            print(f"Tabla {table} borrada." if deleted else f"La tabla {table} no existe.")
        case "cargar-datos":
            print(f"Cargados {_seed(client, table, args.semilla)} ítems en {table}.")
        case "crear-usuario":
            repo.create_user(User(args.usuario, args.nombre, args.email))
            print(f"Usuario {args.usuario} creado.")
        case "usuario":
            found = repo.get_user(args.usuario)
            print(f"{found.username}: {found.name} <{found.email}>")
        case "crear-partida":
            game = repo.create_game(args.creador, args.mapa)
            print(f"Partida {game.game_id} creada en {game.map}.")
        case "abiertas":
            for game in repo.find_open_games(args.mapa, args.limite):
                print(_game_line(game))
        case "partida":
            _print_game(repo.get_game(args.partida))
        case "unirse":
            repo.join_game(args.partida, args.usuario)
            print(f"{args.usuario} se unió a la partida {args.partida}.")
        case "iniciar":
            game = repo.start_game(args.partida, args.creador)
            print(f"Partida {game.game_id} iniciada a las {game.start_time}.")
        case "terminar":
            repo.finish_game(args.partida, args.ranking)
            print(f"Partida {args.partida} terminada; gana {args.ranking[0]}.")
        case "historial":
            _print_history(args.usuario, repo.games_for_user(args.usuario))
        case _:
            _demo(client, table)


def main(argv: Sequence[str] | None = None, client: DynamoDBClient | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        run(args, client or make_client())
    except BattleRoyaleError as error:
        print(f"error: {error}", file=sys.stderr)

        return EXIT_DOMAIN_ERROR

    return 0
