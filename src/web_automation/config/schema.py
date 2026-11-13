"""Pydantic schema for automation JSON files.

The file is data only: every step is one of a closed set of actions with typed,
bounded parameters. Unknown actions, unknown fields (typos), and missing required
parameters are rejected before the browser is launched.
"""

import json
from typing import Annotated, Any, Literal, get_args
from urllib.parse import urlsplit

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StringConstraints,
    model_validator,
)

from web_automation.actions.base import Step

_SECRET_HINTS = ("password", "passcode")


def _check_http_url(value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError("must be an absolute http:// or https:// URL")
    return value


HttpUrl = Annotated[str, AfterValidator(_check_http_url)]
Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,64}$")]
VariableName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")]
EnvVarName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")]
Text = Annotated[str, StringConstraints(min_length=1, max_length=2000)]
TimeoutMs = Annotated[int, Field(strict=True, gt=0, le=600_000)]
PauseMs = Annotated[int, Field(strict=True, gt=0, le=60_000)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- selectors
class _SelectorBase(_Strict):
    # Search only inside the smallest <div> containing this text and the target (popups).
    within_text: Text | None = None


class RoleSelector(_SelectorBase):
    type: Literal["role"]
    role: Text
    name: Text | None = None
    exact: StrictBool = False
    starts_with: StrictBool = False  # name must START with the text (avoids substring clashes)

    @model_validator(mode="after")
    def _starts_with_rules(self) -> "RoleSelector":
        if self.starts_with and self.name is None:
            raise ValueError("'starts_with' requires 'name'")
        if self.starts_with and self.exact:
            raise ValueError("'exact' and 'starts_with' cannot be combined")
        return self


class LabelSelector(_SelectorBase):
    type: Literal["label"]
    value: Text
    exact: StrictBool = False


class PlaceholderSelector(_SelectorBase):
    type: Literal["placeholder"]
    value: Text
    exact: StrictBool = False


class TextSelector(_SelectorBase):
    type: Literal["text"]
    value: Text
    exact: StrictBool = False


class CssSelector(_SelectorBase):
    type: Literal["css"]
    value: Text


class XPathSelector(_SelectorBase):
    type: Literal["xpath"]
    value: Text

    @model_validator(mode="after")
    def _no_scope(self) -> "XPathSelector":
        if self.within_text is not None:
            raise ValueError("'within_text' is not supported with xpath selectors")
        return self


Selector = Annotated[
    RoleSelector | LabelSelector | PlaceholderSelector | TextSelector | CssSelector | XPathSelector,
    Field(discriminator="type"),
]


# -------------------------------------------------------------------- steps
class WhenCondition(_Strict):
    """Run the step only if a variable saved by an earlier step (save_as) matches."""

    variable: VariableName
    equals: Text | None = None
    not_equals: Text | None = None

    @model_validator(mode="after")
    def _exactly_one_test(self) -> "WhenCondition":
        if (self.equals is None) == (self.not_equals is None):
            raise ValueError("'when' needs exactly one of 'equals' or 'not_equals'")
        return self


class RetryPolicy(_Strict):
    """Repeat the step after a failure. Only allowed for actions that are safe to repeat."""

    max_attempts: Annotated[int, Field(strict=True, ge=2, le=5)]
    delay_ms: Annotated[int, Field(strict=True, ge=0, le=60_000)] | None = None


class _StepBase(_Strict):
    id: Identifier
    description: Text | None = None  # documentation only; not passed to the action
    when: WhenCondition | None = None  # conditional step; not passed to the action
    retry: RetryPolicy | None = None  # repeat after failure; not passed to the action


class _TimedStep(_StepBase):
    timeout: TimeoutMs | None = None


class NavigateStep(_TimedStep):
    action: Literal["navigate"]
    url: HttpUrl
    wait_until: Literal["commit", "domcontentloaded", "load", "networkidle"] | None = None


class ClickStep(_TimedStep):
    action: Literal["click"]
    selector: Selector


class FillStep(_TimedStep):
    action: Literal["fill"]
    selector: Selector
    value: Text | None = None
    value_env: EnvVarName | None = None
    optional: StrictBool | None = None  # skip silently if the field never appears
    submit: StrictBool | None = None  # press Enter after filling

    @model_validator(mode="after")
    def _one_source(self) -> "FillStep":
        if (self.value is None) == (self.value_env is None):
            raise ValueError("fill needs exactly one of 'value' or 'value_env'")
        if self.value is not None:
            described = json.dumps(self.selector.model_dump()).lower()
            if any(hint in described for hint in _SECRET_HINTS):
                raise ValueError(
                    "secrets must not be written in the JSON file; use 'value_env' "
                    "to read the value from an environment variable"
                )
        return self


class SelectStep(_TimedStep):
    action: Literal["select"]
    selector: Selector
    value: Text | None = None
    label: Text | None = None

    @model_validator(mode="after")
    def _one_option(self) -> "SelectStep":
        if (self.value is None) == (self.label is None):
            raise ValueError("select needs exactly one of 'value' or 'label'")
        return self


class WaitStep(_TimedStep):
    action: Literal["wait"]
    selector: Selector | None = None
    state: Literal["attached", "detached", "hidden", "visible"] | None = None
    url_contains: Text | None = None
    load_state: Literal["domcontentloaded", "load", "networkidle"] | None = None
    ms: PauseMs | None = None

    @model_validator(mode="after")
    def _one_condition(self) -> "WaitStep":
        given = [n for n in ("selector", "url_contains", "load_state", "ms") if getattr(self, n) is not None]
        if len(given) != 1:
            raise ValueError("wait needs exactly one of: selector, url_contains, load_state, ms")
        if self.state is not None and self.selector is None:
            raise ValueError("'state' is only valid together with 'selector'")
        return self


class ExtractTextStep(_TimedStep):
    action: Literal["extract_text"]
    selector: Selector
    save_as: VariableName


class ExtractAttributeStep(_TimedStep):
    action: Literal["extract_attribute"]
    selector: Selector
    attribute: Text
    save_as: VariableName


class VerifyStep(_TimedStep):
    action: Literal["verify"]
    selector: Selector
    contains: Text | None = None
    contains_env: EnvVarName | None = None

    @model_validator(mode="after")
    def _one_source(self) -> "VerifyStep":
        if (self.contains is None) == (self.contains_env is None):
            raise ValueError("verify needs exactly one of 'contains' or 'contains_env'")
        return self


_RelativePath = Annotated[str, StringConstraints(min_length=1, max_length=200)]


def _check_relative_path(value: str) -> str:
    text = value.replace("\\", "/")
    if text.startswith("/") or ":" in text or any(p in ("", ".", "..") for p in text.split("/")):
        raise ValueError("must be a relative path inside the export folder (no '..', no drive letters)")
    return value


SafeRelativePath = Annotated[_RelativePath, AfterValidator(_check_relative_path)]


class DownloadLatestExportStep(_TimedStep):
    action: Literal["download_latest_export"]
    selector: Selector  # the export dialog
    row_contains: Annotated[list[Text], Field(min_length=1, max_length=10)]
    save_dir: SafeRelativePath
    save_as: VariableName  # variable that receives the saved file's path
    extension: Annotated[str, StringConstraints(pattern=r"^\.[A-Za-z0-9]{1,8}$")] | None = None
    password_env: EnvVarName | None = None
    popup_title: Text | None = None  # title of Meta's second "Download your files" popup
    delete_old: StrictBool | None = None  # after a verified download, delete older files of that type
    download_timeout: TimeoutMs | None = None


class SyncFollowersStep(_StepBase):
    action: Literal["sync_followers"]
    source: VariableName  # variable holding the downloaded zip's path
    member: SafeRelativePath
    account_env: EnvVarName  # environment variable naming the Instagram account
    max_mb: Annotated[int, Field(strict=True, gt=0, le=500)] | None = None


class ExtractFromZipStep(_StepBase):
    action: Literal["extract_from_zip"]
    source: VariableName
    member: SafeRelativePath
    dest: SafeRelativePath
    validate_json: StrictBool | None = None
    max_mb: Annotated[int, Field(strict=True, gt=0, le=2000)] | None = None


class ClickIfVisibleStep(_TimedStep):
    action: Literal["click_if_visible"]
    selector: Selector


class AbortIfVisibleStep(_TimedStep):
    action: Literal["abort_if_visible"]
    selector: Selector
    message: Text


class SetCheckboxesStep(_TimedStep):
    action: Literal["set_checkboxes"]
    selector: Selector  # the container holding the checkboxes (e.g. the dialog)
    checked: Annotated[list[Text], Field(min_length=1, max_length=100)]
    only: StrictBool | None = None  # default true: every other checkbox is cleared

    @model_validator(mode="after")
    def _unique_names(self) -> "SetCheckboxesStep":
        if len(set(self.checked)) != len(self.checked):
            raise ValueError("'checked' must not contain duplicate names")
        return self


class EnterPasswordStep(_TimedStep):
    action: Literal["enter_password"]
    value_env: EnvVarName  # the password itself is never allowed in the JSON
    field_name: Text | None = None
    button_name: Text | None = None


class EnsureLoginStep(_StepBase):
    action: Literal["ensure_login"]
    timeout_s: Annotated[int, Field(strict=True, gt=0, le=3600)] | None = None
    save_as: VariableName | None = None  # receives "logged_in" or "already_authenticated"


class PauseForUserStep(_StepBase):
    action: Literal["pause_for_user"]
    message: Text | None = None


class ConfirmStep(_StepBase):
    action: Literal["confirm"]
    message: Text


StepConfig = Annotated[
    NavigateStep
    | ClickStep
    | FillStep
    | SelectStep
    | WaitStep
    | ExtractTextStep
    | ExtractAttributeStep
    | VerifyStep
    | DownloadLatestExportStep
    | SyncFollowersStep
    | ExtractFromZipStep
    | ClickIfVisibleStep
    | AbortIfVisibleStep
    | SetCheckboxesStep
    | EnterPasswordStep
    | EnsureLoginStep
    | PauseForUserStep
    | ConfirmStep,
    Field(discriminator="action"),
]

# action name -> schema model; a test keeps this in sync with the action registry
STEP_MODELS: dict[str, type[_StepBase]] = {
    get_args(model.model_fields["action"].annotation)[0]: model
    for model in get_args(get_args(StepConfig)[0])
}
SUPPORTED_ACTIONS = tuple(STEP_MODELS)


# ------------------------------------------------------------- whole config
class BrowserSettings(_Strict):
    type: Literal["chrome"] = "chrome"
    headless: StrictBool = False
    persistent: Literal[True] = True  # only persistent profiles are supported


class TargetSettings(_Strict):
    url: HttpUrl  # informational: navigation happens only through explicit steps


class ExecutionSettings(_Strict):
    timeout: TimeoutMs = 30_000
    on_failure: Literal["stop", "continue"] = "stop"


class AutomationConfig(_Strict):
    name: Identifier
    version: Annotated[str, StringConstraints(min_length=1, max_length=32)] = "1.0"
    browser: BrowserSettings = Field(default_factory=BrowserSettings)
    target: TargetSettings | None = None
    steps: list[StepConfig] = Field(min_length=1, max_length=500)
    execution: ExecutionSettings = Field(default_factory=ExecutionSettings)

    @model_validator(mode="after")
    def _unique_step_ids(self) -> "AutomationConfig":
        seen: set[str] = set()
        for step in self.steps:
            if step.id in seen:
                raise ValueError(f"duplicate step id '{step.id}'")
            seen.add(step.id)
        return self

    @model_validator(mode="after")
    def _retry_only_on_repeatable_actions(self) -> "AutomationConfig":
        from web_automation.actions import get_action  # local: actions import this module's peers
        from web_automation.actions.base import RETRY_NEVER

        for step in self.steps:
            if step.retry is not None and get_action(step.action).retry_policy == RETRY_NEVER:
                raise ValueError(
                    f"step '{step.id}': action '{step.action}' cannot be retried, because "
                    "repeating it could repeat a side effect (submit, password, download, "
                    "database write)"
                )
        return self

    @model_validator(mode="after")
    def _when_uses_known_variables(self) -> "AutomationConfig":
        defined: set[str] = set()
        for step in self.steps:
            if step.when is not None and step.when.variable not in defined:
                raise ValueError(
                    f"step '{step.id}': 'when' uses variable '{step.when.variable}', which no "
                    "earlier step sets with save_as"
                )
            saved = getattr(step, "save_as", None)
            if saved:
                defined.add(saved)
        return self

    def to_steps(self) -> list[Step]:
        """Convert to the engine's step objects (parameters only; None values dropped)."""
        steps: list[Step] = []
        for step in self.steps:
            params: dict[str, Any] = step.model_dump(
                mode="json", exclude_none=True,
                exclude={"id", "action", "description", "when", "retry"},
            )
            when = step.when.model_dump(exclude_none=True) if step.when else None
            retry = step.retry.model_dump(exclude_none=True) if step.retry else None
            steps.append(Step(id=step.id, action=step.action, params=params, when=when, retry=retry))
        return steps
