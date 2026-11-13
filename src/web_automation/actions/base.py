from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

from web_automation.utils.exceptions import ActionError, UnknownActionError

if TYPE_CHECKING:
    from web_automation.engine.context import ExecutionContext


# What may be repeated if an action fails (README section 9: "retry policies for safe,
# repeatable actions"). Every action declares one; the default is the safest.
RETRY_NEVER = "never"  # has side effects that must not be repeated (password, download, DB, ...)
RETRY_PRE_ACTION = "pre_action"  # retry only when the target element was not found yet,
#                                  i.e. before anything was clicked, typed or changed
RETRY_ANY = "any"  # read-only or idempotent: safe to repeat after a browser error or timeout
MAX_RETRY_ATTEMPTS = 5
_RETRY_KEYS = {"max_attempts", "delay_ms"}
DEFAULT_RETRY_DELAY_MS = 1000

_WHEN_KEYS = {"variable", "equals", "not_equals"}


def validate_when(step_id: str, when: Any) -> None:
    """A condition is {"variable": name, "equals": text} or {"variable": name, "not_equals": text}."""
    valid = (
        isinstance(when, dict)
        and not set(when) - _WHEN_KEYS
        and isinstance(when.get("variable"), str)
        and bool(when["variable"])
        and sum(key in when for key in ("equals", "not_equals")) == 1
        and isinstance(when.get("equals", when.get("not_equals")), str)
    )
    if not valid:
        raise ActionError(
            f"Step '{step_id}' has an invalid 'when'; use "
            '{"variable": "name", "equals": "text"} or {"variable": "name", "not_equals": "text"}'
        )


def condition_met(when: dict[str, Any], variables: dict[str, str]) -> bool:
    """Evaluate a `when` condition. A variable that was never set counts as 'no value':
    it equals nothing and differs from everything."""
    value = variables.get(when["variable"])
    if "equals" in when:
        return value == when["equals"]
    return value != when["not_equals"]


@dataclass
class Step:
    """One instruction: an id, an action name, and that action's parameters.

    `when` makes the step conditional on a variable saved by an earlier step (`save_as`).
    `retry` ({"max_attempts": 2-5, "delay_ms": optional}) opts a step in to being repeated
    after a failure, only if its action allows it (see RETRY_* above).
    """

    id: str
    action: str
    params: dict[str, Any] = field(default_factory=dict)
    when: dict[str, Any] | None = None
    retry: dict[str, Any] | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Step":
        params = dict(data)
        step_id, action = params.pop("id", None), params.pop("action", None)
        when = params.pop("when", None)
        retry = params.pop("retry", None)
        if not isinstance(step_id, str) or not step_id:
            raise ActionError(f"Step is missing a non-empty 'id': {data!r}")
        if not isinstance(action, str) or not action:
            raise ActionError(f"Step '{step_id}' is missing an 'action'")
        return cls(id=step_id, action=action, params=params, when=when, retry=retry)


class Action(ABC):
    """A single reusable browser operation. Subclass, set `name`, and register."""

    name: ClassVar[str]
    retry_policy: ClassVar[str] = RETRY_NEVER  # opt in per action, only when repeating is safe

    @abstractmethod
    def run(self, ctx: "ExecutionContext", params: dict[str, Any]) -> None:
        """Perform the action. Raise an exception on failure; never swallow errors."""


_REGISTRY: dict[str, Action] = {}


def register_action(cls: type[Action]) -> type[Action]:
    """Class decorator that adds an action to the registry."""
    if cls.name in _REGISTRY:
        raise ValueError(f"Action '{cls.name}' is already registered")
    _REGISTRY[cls.name] = cls()
    return cls


def get_action(name: str) -> Action:
    try:
        return _REGISTRY[name]
    except KeyError:
        known = ", ".join(sorted(_REGISTRY))
        raise UnknownActionError(f"Unknown action '{name}'. Supported: {known}") from None


def registered_actions() -> list[str]:
    return sorted(_REGISTRY)


def validate_retry(step_id: str, action_name: str, retry: Any) -> None:
    """Reject a retry setting that is malformed, or on an action that must never be repeated."""
    if retry is None:
        return
    if get_action(action_name).retry_policy == RETRY_NEVER:
        raise ActionError(
            f"Step '{step_id}': action '{action_name}' cannot be retried, because repeating it "
            "could repeat a side effect (submit, password, download, database write)"
        )
    attempts = retry.get("max_attempts") if isinstance(retry, dict) else None
    delay = retry.get("delay_ms", DEFAULT_RETRY_DELAY_MS) if isinstance(retry, dict) else None
    valid = (
        isinstance(retry, dict)
        and not set(retry) - _RETRY_KEYS
        and isinstance(attempts, int) and not isinstance(attempts, bool)
        and 2 <= attempts <= MAX_RETRY_ATTEMPTS
        and isinstance(delay, int) and not isinstance(delay, bool)
        and 0 <= delay <= 60_000
    )
    if not valid:
        raise ActionError(
            f"Step '{step_id}' has an invalid 'retry'; use "
            f'{{"max_attempts": 2-{MAX_RETRY_ATTEMPTS}, "delay_ms": 0-60000 (optional)}}'
        )


def retry_settings(retry: dict[str, Any] | None) -> tuple[int, int]:
    """(total attempts, delay in ms between attempts) for a validated retry setting."""
    if not retry:
        return 1, 0
    return retry["max_attempts"], retry.get("delay_ms", DEFAULT_RETRY_DELAY_MS)


def require(params: dict[str, Any], key: str, expected: type = str) -> Any:
    value = params.get(key)
    if not isinstance(value, expected) or (expected is str and not value):
        raise ActionError(f"Missing or invalid '{key}' (expected {expected.__name__})")
    return value


def step_timeout(ctx: "ExecutionContext", params: dict[str, Any]) -> int:
    timeout = params.get("timeout", ctx.timeout_ms)
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
        raise ActionError("'timeout' must be a positive integer (milliseconds)")
    return timeout
