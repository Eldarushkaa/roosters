"""Expected failures carry stable API codes, never raw SQL messages."""


class GameError(Exception):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status

