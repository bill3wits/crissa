"""rail-stt — standalone speech-to-text. No fleet / Crissa imports."""

__version__ = "0.1.0"

from .pipeline import Transcriber, Transcript

__all__ = ["Transcriber", "Transcript", "__version__"]
