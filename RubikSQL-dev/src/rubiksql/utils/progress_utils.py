__all__ = [
    "RubikSQLTqdmProgress",
    "RubikSQLRichProgress",
    "RubikSQLSilentProgress",
]

from typing import Any, Dict, Optional

from ahvn.utils.basic.progress_utils import TqdmProgress, Progress as AHVNProgress


class RubikSQLTqdmProgress(TqdmProgress):
    """Single progress bar that maps RubikSQL build emits to tqdm updates."""

    def __init__(self, progress_scale: int = 10000, **kwargs):
        super().__init__(total=progress_scale, desc=kwargs.pop("desc", "RubikSQL"), **kwargs)
        self._scale = progress_scale

    def emit(self, payload: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not payload:
            return None

        event: Dict[str, Any] = dict(payload)

        progress_value = event.pop("progress", None)
        if progress_value is not None and self.total:
            target = max(0, min(self._scale, int(progress_value * self._scale)))
            delta = target - self._n
            if delta != 0:
                event["update"] = delta

        message = event.get("message")
        step = event.get("step")
        desc = message or step
        if desc is not None:
            event.setdefault("description", desc)

        postfix_parts = []
        status = event.get("status")
        if status:
            postfix_parts.append(f"status={status}")
        if step:
            step_current = event.get("step_current")
            step_total = event.get("step_total")
            if step_current is not None and step_total is not None:
                postfix_parts.append(f"step={step}({step_current}/{step_total})")
            else:
                postfix_parts.append(f"step={step}")
        step_progress = event.get("step_progress")
        if step_progress is not None:
            postfix_parts.append(f"step_progress={step_progress * 100:5.2f}%")
        if postfix_parts:
            pct_target = event.get("update", 0) + self._n
            if progress_value is not None:
                pct_target = max(0, min(self._scale, int(progress_value * self._scale)))
            pct = (pct_target / self._scale) * 100 if self._scale else 0.0
            pct_str = f"{pct:5.2f}%"
            event["postfix_dict"] = {"info": ", ".join(postfix_parts), "pct": pct_str}

        event.setdefault("refresh", True)

        # Standardized emit payload now uses `update` plus optional description/postfix.
        return super().emit(event)


class RubikSQLRichProgress(AHVNProgress):
    """Rich-based progress display for RubikSQL builds.

    This is the default progress display for library usage, providing
    a nice rich progress bar with spinner and detailed status updates.
    """

    # Class-level shared progress instance for event updates
    _shared_instance: Optional["RubikSQLRichProgress"] = None

    def __init__(self, **kwargs):
        from rich.console import Console
        from rich.progress import (
            Progress,
            SpinnerColumn,
            TextColumn,
            BarColumn,
            TaskProgressColumn,
            TimeElapsedColumn,
        )

        # Accept and ignore any kwargs (like 'desc') for compatibility
        super().__init__(**kwargs)
        self._console = Console()
        self._Progress = Progress
        self._SpinnerColumn = SpinnerColumn
        self._TextColumn = TextColumn
        self._BarColumn = BarColumn
        self._TaskProgressColumn = TaskProgressColumn
        self._TimeElapsedColumn = TimeElapsedColumn
        self._rich_progress: Optional[Any] = None
        self._task_id = None
        self._entered = False

    def __enter__(self):
        self._rich_progress = self._Progress(
            self._SpinnerColumn(),
            self._TextColumn("[bold blue]{task.description}"),
            self._BarColumn(bar_width=40),
            self._TaskProgressColumn(),
            self._TimeElapsedColumn(),
            console=self._console,
            expand=False,
        )
        self._rich_progress.__enter__()
        self._task_id = self._rich_progress.add_task("Initializing...", total=100)
        self._entered = True
        RubikSQLRichProgress._shared_instance = self
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        RubikSQLRichProgress._shared_instance = None
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
        instance = RubikSQLRichProgress._shared_instance
        if instance and instance._entered:
            instance._update_from_event(payload)

        return payload

    def _update_from_event(self, event: Dict[str, Any]) -> None:
        """Update progress display from a build_stream event."""
        if not self._rich_progress or self._task_id is None:
            return

        status = event.get("status", "building")
        progress = event.get("progress", 0.0)
        message = event.get("message", "")
        step = event.get("step", "")
        step_current = event.get("step_current")
        step_total = event.get("step_total")

        # Build description - use simple messages
        step_text = step.replace("kb.step.", "") if step else ""
        msg_text = message.replace("kb.", "") if message else ""

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


class RubikSQLSilentProgress(AHVNProgress):
    """Silent progress that suppresses all output.

    Use this when you want to run builds without any visual feedback.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False

    def update_total(self, total: Optional[int]) -> None:
        pass

    def update(self, n: int = 1) -> None:
        pass

    def close(self) -> None:
        pass

    def emit(self, payload: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        return payload
