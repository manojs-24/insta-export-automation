class WebAutomationError(Exception):
    """Base class for all engine errors."""


class BrowserLaunchError(WebAutomationError):
    """Chrome could not be launched (not installed, profile locked, ...)."""


class ProfileInUseError(BrowserLaunchError):
    """The automation profile is already open in another Chrome instance."""


class UnsafeProfileError(WebAutomationError):
    """The requested profile directory is the user's everyday Chrome profile."""


class BrowserNotRunningError(WebAutomationError):
    """An operation needs a running browser, but none is open."""


class LoginTimeoutError(WebAutomationError):
    """Login (including any OTP / verification step) did not finish in time."""


class ActionError(WebAutomationError):
    """A step's action failed (bad parameters, element missing, ...)."""


class UnknownActionError(ActionError):
    """A step names an action that is not registered."""


class SelectorError(ActionError):
    """A selector spec is malformed or unsupported."""


class UserDeclinedError(ActionError):
    """The user answered 'no' to a confirm step. Always stops the run."""


class ConfigError(WebAutomationError):
    """A configuration file is missing, is not valid JSON, or fails schema validation."""


class ElementNotFoundError(ActionError):
    """The element did not reach the required state within the timeout."""


class WorkflowStopped(ActionError):
    """A guard decided the workflow must not continue (not an error: nothing went wrong).

    Example: an export request is already pending, so submitting again would duplicate it.
    """


class DatabaseError(WebAutomationError):
    """A database operation failed. Messages never contain the connection URL or password."""
