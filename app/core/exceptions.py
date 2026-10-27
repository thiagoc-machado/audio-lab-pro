class AudioError(Exception):
    def __init__(self, message: str, status: int = 422):
        self.message = message
        self.status = status
        super().__init__(message)


class Cancelled(AudioError):
    def __init__(self):
        super().__init__("Job cancelled", 409)
