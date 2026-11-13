import logging

_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"web_automation.{name}")


def configure_logging(level: int = logging.INFO) -> None:
    """Send the 'web_automation' logs to the console. Safe to call more than once.

    Nothing is written to a log file: every run's log lines are stored in the database
    (see db/history.py), which is the permanent record.
    """
    root = logging.getLogger("web_automation")
    root.setLevel(level)
    if any(getattr(h, "_wa_console", False) for h in root.handlers):
        return
    console = logging.StreamHandler()
    console._wa_console = True  # type: ignore[attr-defined]  # so the run recorder's handler doesn't count
    console.setFormatter(logging.Formatter(_FORMAT))
    root.addHandler(console)
