"""Domain errors raised when a conditional write is rejected."""


class BattleRoyaleError(Exception):
    """Base class for every rule the table enforces."""


class UserAlreadyExistsError(BattleRoyaleError):
    def __init__(self, username: str) -> None:
        super().__init__(f"user {username} already exists")
        self.username = username


class UserNotFoundError(BattleRoyaleError):
    def __init__(self, username: str) -> None:
        super().__init__(f"user {username} not found")
        self.username = username


class GameNotFoundError(BattleRoyaleError):
    def __init__(self, game_id: str) -> None:
        super().__init__(f"game {game_id} not found")
        self.game_id = game_id


class AlreadyJoinedError(BattleRoyaleError):
    def __init__(self, game_id: str, username: str) -> None:
        super().__init__(f"user {username} already joined game {game_id}")
        self.game_id = game_id
        self.username = username


class GameFullError(BattleRoyaleError):
    def __init__(self, game_id: str) -> None:
        super().__init__(f"game {game_id} is full")
        self.game_id = game_id


class GameAlreadyStartedError(BattleRoyaleError):
    def __init__(self, game_id: str) -> None:
        super().__init__(f"game {game_id} already started")
        self.game_id = game_id


class GameNotStartedError(BattleRoyaleError):
    def __init__(self, game_id: str) -> None:
        super().__init__(f"game {game_id} has not started")
        self.game_id = game_id


class GameAlreadyFinishedError(BattleRoyaleError):
    def __init__(self, game_id: str) -> None:
        super().__init__(f"game {game_id} already finished")
        self.game_id = game_id


class GameNotFullError(BattleRoyaleError):
    def __init__(self, game_id: str, people: int) -> None:
        super().__init__(f"game {game_id} has {people} of the players it needs")
        self.game_id = game_id
        self.people = people


class NotGameCreatorError(BattleRoyaleError):
    def __init__(self, game_id: str, username: str) -> None:
        super().__init__(f"user {username} did not create game {game_id}")
        self.game_id = game_id
        self.username = username


class PlayerNotInGameError(BattleRoyaleError):
    def __init__(self, game_id: str, username: str) -> None:
        super().__init__(f"user {username} is not playing game {game_id}")
        self.game_id = game_id
        self.username = username


class InvalidRankingError(BattleRoyaleError):
    def __init__(self, reason: str) -> None:
        super().__init__(f"invalid ranking: {reason}")
