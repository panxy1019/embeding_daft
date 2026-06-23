"""\
Rich-based progress display for RubikSQL CLI.
"""

__all__ = ["RubikSQLCLIProgress"]

from typing import Any, Dict, Optional

from rich.console import Console
from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    BarColumn,
    TaskProgressColumn,
    TimeElapsedColumn,
)

from ahvn.utils.basic.progress_utils import Progress as AHVNProgress
from ahvn.utils.basic.log_utils import SuppressOutput


# Message translations for CLI display
MESSAGE_TRANSLATIONS = {
    "kb.alreadyBuilt": "Knowledge base already built",
    "kb.clearingOldKnowledge": "Clearing old knowledge...",
    "kb.clearingProgress": "Clearing old knowledge",
    "kb.clearedOldKnowledge": "Cleared old knowledge",
    "kb.counting": "Counting tables and columns...",
    "kb.database": "Extracting database metadata...",
    "kb.extractedTables": "Extracted tables",
    "kb.extractedColumns": "Extracted columns",
    "kb.extractedEnums": "Extracted enums",
    "kb.generatingDescs": "Generating descriptions...",
    "kb.generatingSynonyms": "Generating synonyms...",
    "kb.upserting": "Saving knowledge...",
    "kb.buildingDaac": "Building search index...",
    "kb.daacBuilt": "Search index built",
    "kb.success": "Build completed successfully!",
}

# Step translations
STEP_TRANSLATIONS = {
    "kb.step.clearKls": "Clear",
    "kb.step.counting": "Count",
    "kb.step.databaseKl": "Database",
    "kb.step.tableKls": "Tables",
    "kb.step.columnKls": "Columns",
    "kb.step.enumKls": "Enums",
    "kb.step.generateDescs": "Descriptions",
    "kb.step.generateSynonyms": "Synonyms",
    "kb.step.upsertAll": "Save",
    "kb.step.daacEngine": "Index",
    "kb.step.completed": "Done",
}


def _translate_message(msg: str) -> str:
    """Translate a message key to display text."""
    if not msg:
        return ""
    # Handle parameterized messages like "kb.buildingTable:tablename"
    if ":" in msg:
        key, param = msg.split(":", 1)
        base = MESSAGE_TRANSLATIONS.get(key, key)
        return f"{base}: {param}"
    return MESSAGE_TRANSLATIONS.get(msg, msg)


def _translate_step(step: str) -> str:
    """Translate a step key to display text."""
    return STEP_TRANSLATIONS.get(step, step) if step else ""


class RubikSQLCLIProgress(AHVNProgress):
    """Rich-based progress display for CLI KB builds.

    This class can be used both as a context manager for direct event handling
    and as a progress class passed to build_stream.
    """

    # Class-level shared progress instance for event updates
    _shared_instance: Optional["RubikSQLCLIProgress"] = None

    def __init__(self, **kwargs):
        # Accept and ignore any kwargs (like 'desc') for compatibility
        super().__init__(**kwargs)
        self.console = Console()
        self._rich_progress: Optional[Progress] = None
        self._task_id = None
        self._entered = False

    def __enter__(self):
        self._rich_progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(bar_width=40),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            console=self.console,
            expand=False,
        )
        self._rich_progress.__enter__()
        self._task_id = self._rich_progress.add_task("Initializing...", total=100)
        self._entered = True
        RubikSQLCLIProgress._shared_instance = self
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        RubikSQLCLIProgress._shared_instance = None
        self._entered = False
        if self._rich_progress:
            self._rich_progress.__exit__(exc_type, exc_val, exc_tb)
        return False

    def update_total(self, total: Optional[int]) -> None:
        """Update the total iterations for the progress bar."""
        self._total = total
        if self._rich_progress and self._task_id is not None:
            self._rich_progress.update(self._task_id, total=total)

    def update(self, n: int = 1) -> None:
        """Update the progress bar by n steps."""
        self._n += n
        if self._rich_progress and self._task_id is not None:
            self._rich_progress.update(self._task_id, advance=n)

    def close(self) -> None:
        """Close and cleanup the progress bar."""
        self._closed = True
        if self._rich_progress:
            self._rich_progress.stop()

    def emit(self, payload: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Process event from build_stream and update display."""
        if payload is None:
            return None

        # Update the shared instance if available
        instance = RubikSQLCLIProgress._shared_instance
        if instance and instance._entered:
            instance.update_from_event(payload)

        return payload

    def update_from_event(self, event: Dict[str, Any]) -> None:
        """Update progress display from a build_stream event."""
        if not self._rich_progress or self._task_id is None:
            return

        status = event.get("status", "building")
        progress = event.get("progress", 0.0)
        message = event.get("message", "")
        step = event.get("step", "")
        step_current = event.get("step_current")
        step_total = event.get("step_total")

        # Build description
        step_text = _translate_step(step)
        msg_text = _translate_message(message)

        # Build detailed description
        if step_current is not None and step_total is not None:
            desc = f"[{step_text}] {msg_text} ({step_current}/{step_total})"
        elif msg_text:
            desc = f"[{step_text}] {msg_text}" if step_text else msg_text
        else:
            desc = step_text or "Processing..."

        # Update progress bar with refresh=True to force spinner animation
        completed = int(progress * 100)
        self._rich_progress.update(self._task_id, completed=completed, description=desc, refresh=True)

        # Handle completion
        if status == "completed":
            self._rich_progress.update(self._task_id, completed=100, description="[green]✓ Build completed!")
        elif status == "cancelled":
            self._rich_progress.update(self._task_id, description="[yellow]⚠ Build cancelled")
        elif status == "failed":
            self._rich_progress.update(self._task_id, description="[red]✗ Build failed")
