import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from web_automation.config.schema import AutomationConfig
from web_automation.utils.exceptions import ConfigError

_MAX_BYTES = 1_000_000


def format_validation_error(exc: ValidationError) -> str:
    """Readable multi-line message. Never echoes the offending input values (may be secrets)."""
    lines = [f"Invalid configuration ({exc.error_count()} problem(s)):"]
    for err in exc.errors(include_input=False, include_url=False):
        location = ".".join(str(part) for part in err["loc"]) or "<root>"
        ctx = err.get("ctx", {})
        if err["type"] == "union_tag_invalid":
            message = f"unsupported value; expected one of: {ctx.get('expected_tags', '?')}"
        elif err["type"] == "union_tag_not_found":
            message = f"missing required field {ctx.get('discriminator', '?')}"
        else:
            message = err["msg"].removeprefix("Value error, ")
        lines.append(f"  - {location}: {message}")
    return "\n".join(lines)


def parse_config(data: Any) -> AutomationConfig:
    try:
        return AutomationConfig.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(format_validation_error(exc)) from None


def load_config(path: str | Path) -> AutomationConfig:
    """Read, parse, and validate a JSON automation file. Nothing is executed."""
    file = Path(path)
    try:
        if file.stat().st_size > _MAX_BYTES:
            raise ConfigError(f"Config file is larger than {_MAX_BYTES} bytes: {file}")
        text = file.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigError(f"Config file not found: {file}") from None
    except OSError as exc:
        raise ConfigError(f"Cannot read config file {file}: {exc.strerror}") from None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"{file.name} is not valid JSON: {exc.msg} (line {exc.lineno}, column {exc.colno})"
        ) from None
    if not isinstance(data, dict):
        raise ConfigError(f"{file.name} must contain a JSON object at the top level")
    return parse_config(data)
