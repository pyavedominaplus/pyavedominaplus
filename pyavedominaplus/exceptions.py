"""Exceptions for AVE DominaPlus."""


class AVEDominaError(Exception):
    """Base exception for all AVE DominaPlus errors."""


class AVEDominaConnectionError(AVEDominaError, ConnectionError):
    """Raised when connecting to or communicating with the server fails.

    Also subclasses ConnectionError so callers that catch the builtin
    keep working.
    """


class AVEDominaTimeoutError(AVEDominaConnectionError):
    """Raised when a connection attempt times out."""
