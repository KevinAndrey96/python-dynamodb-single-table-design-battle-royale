from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pytest

from battle_royale import cli, keys, seed
from battle_royale.repository import BattleRoyaleRepository

if TYPE_CHECKING:
    from types_boto3_dynamodb import DynamoDBClient

    from tests.conftest import FullGame


def run(client: DynamoDBClient, capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = cli.main(list(argv), client)
    captured = capsys.readouterr()

    return code, captured.out, captured.err


def test_table_lifecycle(bare_client: DynamoDBClient, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(bare_client, capsys, "crear-tabla")[1] == "Tabla battle-royale creada.\n"
    assert run(bare_client, capsys, "crear-tabla")[1] == "La tabla battle-royale ya existía.\n"
    assert run(bare_client, capsys, "borrar-tabla")[1] == "Tabla battle-royale borrada.\n"
    assert run(bare_client, capsys, "borrar-tabla")[1] == "La tabla battle-royale no existe.\n"


def test_custom_table_name(bare_client: DynamoDBClient, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(bare_client, capsys, "--tabla", "torneo", "crear-tabla")[1] == "Tabla torneo creada.\n"


def test_load_and_browse_the_demo_data(client: DynamoDBClient, capsys: pytest.CaptureFixture[str]) -> None:
    data = seed.generate()
    _, out, _ = run(client, capsys, "cargar-datos")
    assert out == f"Cargados {len(list(data.items()))} ítems en battle-royale.\n"

    _, out, _ = run(client, capsys, "abiertas")
    lines = out.splitlines()
    assert len(lines) == seed.OPEN_GAMES
    assert all("abierta" in line for line in lines)

    finished = next(g for g in data.games if g.end_time)
    _, out, _ = run(client, capsys, "partida", finished.game_id)
    assert "Estado   : terminada (50/50 jugadores)" in out
    assert re.search(r"^\s+1\. \S+$", out, re.MULTILINE)
    assert "Terminada:" in out

    _, out, _ = run(client, capsys, "abiertas", "--mapa", seed.MAPS[0], "--limite", "1")
    assert len(out.splitlines()) <= 1

    waiting = next(g for g in data.games if g.status == "abierta")
    _, out, _ = run(client, capsys, "partida", waiting.game_id)
    assert f"Estado   : abierta ({waiting.people}/50 jugadores)" in out
    assert "Iniciada" not in out
    assert out.count("  - ") == waiting.people


def test_user_commands(client: DynamoDBClient, capsys: pytest.CaptureFixture[str]) -> None:
    created = run(
        client, capsys, "crear-usuario", "ana", "--nombre", "Ana Gómez", "--email", "ana@example.com"
    )
    assert created == (0, "Usuario ana creado.\n", "")
    assert run(client, capsys, "usuario", "ana")[1] == "ana: Ana Gómez <ana@example.com>\n"

    code, out, err = run(
        client, capsys, "crear-usuario", "ana", "--nombre", "Ana", "--email", "a@example.com"
    )
    assert (code, out, err) == (cli.EXIT_DOMAIN_ERROR, "", "error: user ana already exists\n")


def test_game_commands(
    client: DynamoDBClient, capsys: pytest.CaptureFixture[str], full_game: FullGame
) -> None:
    game_id, roster = full_game()

    _, out, _ = run(client, capsys, "crear-partida", roster[0], "Caño Cristales")
    other_id = out.split()[1]
    assert out.endswith("creada en Caño Cristales.\n")
    assert (
        run(client, capsys, "unirse", other_id, roster[1])[1]
        == f"{roster[1]} se unió a la partida {other_id}.\n"
    )

    code, _, err = run(client, capsys, "unirse", game_id, roster[keys.MAX_PLAYERS])
    assert (code, err) == (1, f"error: game {game_id} is full\n")

    assert run(client, capsys, "iniciar", game_id, roster[0])[1].startswith(
        f"Partida {game_id} iniciada a las "
    )
    _, out, _ = run(client, capsys, "terminar", game_id, roster[2], roster[0])
    assert out == f"Partida {game_id} terminada; gana {roster[2]}.\n"

    _, out, _ = run(client, capsys, "partida", game_id)
    assert f" 1. {roster[2]}\n" in out
    assert f" 2. {roster[0]}\n" in out
    assert f"  - {roster[1]}\n" in out

    _, out, _ = run(client, capsys, "historial", roster[0])
    assert out.splitlines()[0] == f"Partidas de {roster[0]}: 2"
    assert f"{game_id}  puesto 2" in out
    assert f"{other_id}  sin puesto" in out


def test_demo_walks_every_access_pattern(
    bare_client: DynamoDBClient, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, err = run(bare_client, capsys, "demo")

    assert (code, err) == (0, "")
    assert "Tabla battle-royale creada; cargados" in out
    assert "la escritura condicional lo rechaza: game" in out
    assert "did not create game" in out
    assert "sale del índice de partidas abiertas" in out
    winner = re.search(r"gana (\S+)\.", out)
    assert winner is not None
    assert f"Partidas de {winner.group(1)}:" in out

    second_code, second_out, _ = run(bare_client, capsys, "demo")
    assert second_code == 0
    assert "creada; cargados" not in second_out
    assert len(BattleRoyaleRepository(bare_client).find_open_games(limit=100)) >= seed.OPEN_GAMES


def test_requires_a_command(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        cli.main([])

    assert caught.value.code == 2
    assert "comando" in capsys.readouterr().err


def test_make_client_reads_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DYNAMODB_ENDPOINT_URL", "http://localhost:8000")
    monkeypatch.setenv("AWS_REGION", "sa-east-1")

    client = cli.make_client()
    assert client.meta.endpoint_url == "http://localhost:8000"
    assert client.meta.region_name == "sa-east-1"

    monkeypatch.delenv("DYNAMODB_ENDPOINT_URL")
    monkeypatch.delenv("AWS_REGION")
    monkeypatch.delenv("AWS_DEFAULT_REGION")
    assert cli.make_client().meta.region_name == "us-east-1"
