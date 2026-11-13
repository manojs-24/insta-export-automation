"""Derive the README's distinct outcomes from what the steps actually did.

README section 9: "Execution completed", "Export request submitted", "Export request
acknowledged" and "Export file ready for download" are different facts, and clicking the
final button must not be reported as a successful export. Each fact below is true only
when its own step finished with status "ok".
"""

from web_automation.engine.executor import ExecutionResult

# Step ids of the Instagram workflow that prove each fact (see configs/instagram_export.json).
SUBMIT_STEP = "start_export"
ACKNOWLEDGED_STEP = "request_acknowledged"
DOWNLOAD_STEP = "download_latest_export"


def _ok(result: ExecutionResult, step_id: str) -> bool:
    return any(s.id == step_id and s.status == "ok" for s in result.steps)


def build_facts(result: ExecutionResult) -> dict[str, bool | str]:
    submitted = _ok(result, SUBMIT_STEP)
    acknowledged = submitted and _ok(result, ACKNOWLEDGED_STEP)
    downloaded = _ok(result, DOWNLOAD_STEP)
    completed = result.status == "success"

    first_problem = next((x.message for x in result.steps if x.status == "failed" and x.message), None)

    if submitted and acknowledged:
        message = "Export request submitted and acknowledged by Instagram"
    elif submitted:
        message = "Export request submitted but NOT acknowledged; check Instagram before running again"
    elif result.status == "stopped":
        message = result.stop_reason or "Stopped before submitting"
    else:
        message = "No export request was submitted"
        if first_problem:
            message += f". Cause: {first_problem}"
    if downloaded:
        message += "; the latest finished export was downloaded"
    return {
        "execution_completed": completed,
        "export_request_submitted": submitted,
        "export_request_acknowledged": acknowledged,
        "export_file_downloaded": downloaded,
        "message": message,
    }
