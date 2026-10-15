"""Engine exceptions. The backend catches EngineError for everything."""


class EngineError(Exception):
    """Base class for every error raised by the engine."""


class InsufficientDataError(EngineError):
    """Not enough rows to compute something reliably (e.g. PSI needs >= 500)."""


class ModelLoadError(EngineError):
    """Model artifact missing, corrupt or incompatible -> backend falls back to rules-only."""


class ConfigError(EngineError):
    """EngineConfig values are invalid (e.g. alpha + beta != 1)."""