"""
FunHarness - TUI Application

Textual-based terminal UI inspired by Claude Code.
Features: bordered tool calls, streaming with accent, thinking animation.
"""
from __future__ import annotations
import json
import threading
import time

from rich.markup import escape

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll, Horizontal
from textual.widgets import Input, Markdown, Static, TextArea, Button
from textual.timer import Timer

from .banner import get_full_banner
from .theme import (
    funharness_theme, ICONS, SPINNER_FRAMES, RISK_CONFIG,
    OCHRE_PRIMARY, OCHRE_BRIGHT, OCHRE_DIM, OCHRE_MUTED,
    SURFACE_DARK, SURFACE_LIGHT, SURFACE_RAISED, PANEL_BG,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_DIM,
    SUCCESS_COLOR, ERROR_COLOR, INFO_COLOR, ACCENT_COOL,
)

STREAM_RENDER_INTERVAL = 1 / 30
REASONING_RENDER_INTERVAL = 1 / 15
TOOL_TIMER_COLOR = "#A8A8A8"
TOOL_SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")


# ================================================================
#  Widget Classes
# ================================================================

class BannerWidget(Static):
    DEFAULT_CSS = """
    BannerWidget {
        height: auto;
        padding: 1 2 0 2;
        margin: 0 0 1 0;
    }
    """


class StatusBar(Static):
    DEFAULT_CSS = f"""
    StatusBar {{
        dock: bottom;
        height: 1;
        background: {SURFACE_RAISED};
        color: {TEXT_SECONDARY};
        padding: 0 2;
    }}
    """

    def update_info(self, mode="suggest", messages=0, tokens=0, cost="\u00a50.0000",
                    team=0, runtime=0, schedules=0):
        self.update(
            f" [{OCHRE_PRIMARY}]FunHarness[/] | "
            f"Mode: [bold {OCHRE_BRIGHT}]{mode}[/] | "
            f"Msgs: {messages} | "
            f"~{tokens:,} tok | "
            f"Team: {team} | Bg: {runtime} | Sched: {schedules} | "
            f"[{ACCENT_COOL}]Cost: {cost}[/]"
        )


class ThinkingIndicator(Static):
    """Animated spinner while waiting for LLM response."""

    DEFAULT_CSS = f"""
    ThinkingIndicator {{
        height: 1;
        padding: 0 2;
        margin: 0 0 0 4;
        color: {TEXT_SECONDARY};
    }}
    """

    def __init__(self):
        super().__init__("")
        self._frame_idx = 0
        self._timer: Timer | None = None

    def on_mount(self) -> None:
        self._timer = self.set_interval(0.12, self._tick)
        self._tick()

    def _tick(self) -> None:
        self._frame_idx = (self._frame_idx + 1) % len(SPINNER_FRAMES)
        frame = SPINNER_FRAMES[self._frame_idx]
        self.update(f"  [{OCHRE_PRIMARY}]{frame}[/] [{TEXT_DIM}]Thinking...[/]")

    def on_unmount(self) -> None:
        if self._timer:
            self._timer.stop()


class StreamingText(Static):
    """Live-updating widget for streaming LLM tokens with left accent border."""

    DEFAULT_CSS = f"""
    StreamingText {{
        height: auto;
        padding: 0 1 0 2;
        margin: 0 2 0 4;
        border-left: thick {OCHRE_PRIMARY};
        color: {TEXT_PRIMARY};
    }}
    """


class ReasoningBlock(Static):
    """Collapsible block showing LLM reasoning/thinking content with spinner.

    Click to toggle expand/collapse. Shows a spinner while streaming.
    """

    DEFAULT_CSS = f"""
    ReasoningBlock {{
        height: auto;
        padding: 0 1 0 2;
        margin: 0 2 0 4;
        border-left: thick {OCHRE_DIM};
        color: {TEXT_DIM};
    }}
    """

    def __init__(self):
        super().__init__("")
        self._reasoning_buffer = ""
        self._is_expanded = True
        self._is_streaming = True
        self._frame_idx = 0
        self._timer: Timer | None = None
        self._last_render_at = 0.0

    def on_mount(self) -> None:
        self._timer = self.set_interval(0.12, self._tick_spinner)

    def _tick_spinner(self) -> None:
        if self._is_streaming:
            self._frame_idx = (self._frame_idx + 1) % len(SPINNER_FRAMES)
            self._render_display()

    def append_token(self, token: str) -> bool:
        self._reasoning_buffer += token
        now = time.monotonic()
        if "\n" in token or now - self._last_render_at >= REASONING_RENDER_INTERVAL:
            self._last_render_at = now
            self._render_display()
            return True
        return False

    def finish_streaming(self) -> None:
        self._is_streaming = False
        self._is_expanded = False
        if self._timer:
            self._timer.stop()
            self._timer = None
        self._render_display()

    def on_click(self) -> None:
        if not self._is_streaming:
            self._is_expanded = not self._is_expanded
            self._render_display()

    def _render_display(self) -> None:
        lines = []
        if self._is_streaming:
            frame = SPINNER_FRAMES[self._frame_idx]
            lines.append(
                f"[{OCHRE_PRIMARY}]{frame}[/] [{TEXT_DIM}]Thinking...[/]"
            )
            if self._reasoning_buffer:
                # Show last few lines while streaming
                text = escape(self._reasoning_buffer)
                tail = text.split("\n")
                display_lines = tail[-6:] if len(tail) > 6 else tail
                display_text = "\n".join(display_lines)
                lines.append(f"[{TEXT_DIM}]{display_text}[/]")
        else:
            char_count = len(self._reasoning_buffer)
            toggle_hint = "[click to expand]" if not self._is_expanded else "[click to collapse]"
            lines.append(
                f"[{OCHRE_MUTED}][Thinking: {char_count} chars] {toggle_hint}[/]"
            )
            if self._is_expanded and self._reasoning_buffer:
                lines.append(f"[{TEXT_DIM}]{escape(self._reasoning_buffer)}[/]")

        self.update("\n".join(lines))

    def on_unmount(self) -> None:
        if self._timer:
            self._timer.stop()


class AssistantMessage(Static):
    """Finalized assistant response rendered as Markdown with left accent border."""

    DEFAULT_CSS = f"""
    AssistantMessage {{
        height: auto;
        padding: 0 1 0 2;
        margin: 0 2 1 4;
        border-left: thick {OCHRE_PRIMARY};
        color: {TEXT_PRIMARY};
    }}
    AssistantMessage Markdown {{
        margin: 0;
        padding: 0;
        color: #D6C8B0;
    }}
    AssistantMessage MarkdownH1 {{
        color: {OCHRE_BRIGHT};
        margin: 0 0 1 0;
        padding: 0;
    }}
    AssistantMessage MarkdownH2 {{
        color: {OCHRE_BRIGHT};
        margin: 0 0 1 0;
        padding: 0;
    }}
    AssistantMessage MarkdownFence {{
        margin: 1 0;
        max-height: 20;
    }}
    """

    def __init__(self, md_text: str, **kwargs):
        super().__init__(**kwargs)
        self._md_text = md_text

    def compose(self) -> ComposeResult:
        md = Markdown(self._md_text)
        md.code_indent_guides = False
        yield md


# File extension -> TextArea language mapping
_EXT_LANG_MAP = {
    ".py": "python", ".js": "javascript", ".ts": "javascript",
    ".json": "json", ".yaml": "yaml", ".yml": "yaml",
    ".toml": "toml", ".md": "markdown", ".html": "html",
    ".css": "css", ".sh": "bash", ".bash": "bash",
    ".rs": "rust", ".go": "go", ".java": "java",
    ".c": "c", ".cpp": "c", ".h": "c", ".rb": "ruby",
    ".sql": "sql", ".xml": "xml", ".tex": "latex",
}


class FilePreviewBlock(Static):
    """Collapsible file preview using TextArea (read-only, vscode_dark).

    Shows first 10 lines by default. Click border to expand/collapse.
    """

    DEFAULT_CSS = f"""
    FilePreviewBlock {{
        height: auto;
        margin: 0 2 1 6;
        border: round {OCHRE_MUTED};
        border-title-color: {OCHRE_BRIGHT};
        border-title-style: bold;
        padding: 0;
    }}
    FilePreviewBlock TextArea {{
        max-height: 14;
    }}
    FilePreviewBlock.expanded TextArea {{
        max-height: 40;
    }}
    """

    def __init__(self, filepath: str, content: str, **kwargs):
        super().__init__(**kwargs)
        self._filepath = filepath
        self._content = content
        self._expanded = False
        lines = content.split("\n")
        self._preview = "\n".join(lines[:10])
        self._full = content
        self._has_more = len(lines) > 10
        self.border_title = f">>> {filepath}"
        if self._has_more:
            self.border_subtitle = "[+] click to expand"

    def compose(self) -> ComposeResult:
        # Detect language from extension
        import os
        ext = os.path.splitext(self._filepath)[1].lower()
        lang = _EXT_LANG_MAP.get(ext)
        yield TextArea(
            self._preview, language=lang, theme="vscode_dark",
            read_only=True, show_line_numbers=True, show_cursor=False,
        )

    def on_click(self) -> None:
        if not self._has_more:
            return
        self._expanded = not self._expanded
        ta = self.query_one(TextArea)
        ta.load_text(self._full if self._expanded else self._preview)
        if self._expanded:
            self.add_class("expanded")
            self.border_subtitle = "[-] click to collapse"
        else:
            self.remove_class("expanded")
            self.border_subtitle = "[+] click to expand"


class ToolGenBlock(Static):
    """Live-updating widget showing tool argument generation in progress.

    Displays a spinner + tool name + streaming argument preview.
    """

    DEFAULT_CSS = f"""
    ToolGenBlock {{
        height: auto;
        padding: 0 1 0 2;
        margin: 0 2 0 4;
        border-left: thick {OCHRE_MUTED};
        color: {TEXT_DIM};
    }}
    """

    def __init__(self):
        super().__init__(" ")
        self._tool_name = ""
        self._buffer = ""
        self._frame_idx = 0
        self._timer: Timer | None = None
        self._last_render_at = 0.0

    def on_mount(self) -> None:
        self._timer = self.set_interval(0.12, self._tick)

    def set_tool_name(self, name: str) -> None:
        self._tool_name = name

    def append_chunk(self, chunk: str) -> bool:
        self._buffer += chunk
        now = time.monotonic()
        if "\n" in chunk or now - self._last_render_at >= REASONING_RENDER_INTERVAL:
            self._last_render_at = now
            self._refresh_display()
            return True
        return False

    def _tick(self) -> None:
        self._frame_idx = (self._frame_idx + 1) % len(SPINNER_FRAMES)
        self._refresh_display()

    def _refresh_display(self) -> None:
        frame = SPINNER_FRAMES[self._frame_idx]
        header = (
            f"[{OCHRE_PRIMARY}]{frame}[/] "
            f"[{OCHRE_BRIGHT}]{ICONS['tool']}[/] "
            f"[{TEXT_SECONDARY}]Generating {self._tool_name}...[/]"
        )
        lines = [header]
        if self._buffer:
            # Show first 200 chars as preview
            preview = self._buffer[:200]
            if len(self._buffer) > 200:
                preview += "..."
            lines.append(f"[{TEXT_DIM}]{escape(preview)}[/]")
            lines.append(
                f"[{TEXT_DIM}]({len(self._buffer)} chars generated)[/]"
            )
        self.update("\n".join(lines))

    def on_unmount(self) -> None:
        if self._timer:
            self._timer.stop()


class ToolCallBlock(Static):
    """Bordered box showing a tool invocation and its result.

    Tool name + icon displayed in border-title (top-left corner).
    Created with call info, then updated with result via add_result().
    """

    DEFAULT_CSS = f"""
    ToolCallBlock {{
        height: auto;
        padding: 1 2;
        margin: 0 2 1 4;
        border: round {OCHRE_MUTED};
        background: {SURFACE_RAISED};
        border-title-color: {OCHRE_BRIGHT};
        border-title-style: bold;
        border-title-align: left;
    }}
    """

    def __init__(self, call_markup: str, tool_name: str = "",
                 risk_label: str = "", **kwargs):
        super().__init__(call_markup, **kwargs)
        self._call_markup = call_markup
        self._result_markup = ""
        self._hook_markup = ""
        self._tool_name = tool_name
        self._risk_label = risk_label
        self._started_at = time.monotonic()
        self._elapsed = 0.0
        self._timer: Timer | None = None
        self._is_running = True
        self._spinner_idx = 0
        self._update_title()

    def on_mount(self) -> None:
        self._timer = self.set_interval(0.2, self._tick)

    def _tick(self) -> None:
        if not self._is_running:
            return
        self._spinner_idx = (self._spinner_idx + 1) % len(TOOL_SPINNER_FRAMES)
        self._elapsed = time.monotonic() - self._started_at
        self._update_title()

    def _format_elapsed(self) -> str:
        if self._elapsed < 60:
            return f"{self._elapsed:0.1f}s"
        minutes = int(self._elapsed // 60)
        seconds = int(self._elapsed % 60)
        return f"{minutes}m{seconds:02d}s"

    def _update_title(self) -> None:
        if self._is_running:
            timer = f"{TOOL_SPINNER_FRAMES[self._spinner_idx]} {self._format_elapsed()}"
        else:
            timer = self._format_elapsed()
        self.border_title = (
            f"{ICONS['tool']} {self._tool_name}  "
            f"[{self._risk_label}]  [{TOOL_TIMER_COLOR}]({timer})[/]"
        )

    def update_call_markup(self, call_markup: str) -> None:
        """Refresh displayed arguments while keeping the timer running."""
        self._call_markup = call_markup
        if not self._result_markup:
            self.update(call_markup)

    def _stop_timer(self) -> None:
        self._elapsed = time.monotonic() - self._started_at
        self._is_running = False
        if self._timer:
            self._timer.stop()
            self._timer = None
        self._update_title()

    def on_unmount(self) -> None:
        if self._timer:
            self._timer.stop()

    def set_approval_status(self, approved: bool):
        """Update border subtitle to show approval result."""
        if approved:
            self.border_subtitle = "Approved"
            self.styles.border_subtitle_color = SUCCESS_COLOR
        else:
            self.border_subtitle = "Denied"
            self.styles.border_subtitle_color = ERROR_COLOR

    def set_pending_approval(self):
        """Mark this tool block as waiting for user approval."""
        self.border_subtitle = "Pending Approval"
        self.styles.border_subtitle_color = OCHRE_BRIGHT

    def add_result(self, result_markup: str, hook_markup: str = ""):
        self._stop_timer()
        self._result_markup = result_markup
        self._hook_markup = hook_markup
        parts = [self._call_markup]
        parts.append(f"[{OCHRE_MUTED}]{'-' * 46}[/]")
        parts.append(self._result_markup)
        if self._hook_markup:
            parts.append(self._hook_markup)
        self.update("\n".join(parts))


def _format_tool_args_preview(preview: str) -> str:
    """Format tool arguments as a compact one-line tree."""
    try:
        parsed = json.loads(preview)
    except json.JSONDecodeError:
        rendered = preview
    else:
        rendered = json.dumps(parsed, ensure_ascii=False)

    if len(rendered) > 240:
        rendered = rendered[:240] + "..."

    return (
        f"[{OCHRE_MUTED}]└─[/] [{TEXT_SECONDARY}]args[/] "
        f"[{OCHRE_MUTED}]⟼[/] [{TEXT_DIM}]{escape(rendered)}[/]"
    )


class ApprovalPrompt(Horizontal):
    """Inline approval bar with Allow / Always / Deny buttons."""

    DEFAULT_CSS = f"""
    ApprovalPrompt {{
        height: auto;
        layout: horizontal;
        align: right middle;
        margin: 0 2 0 6;
        background: transparent;
    }}
    ApprovalPrompt .approval-label {{
        width: 1fr;
        color: {TEXT_DIM};
        content-align: left middle;
    }}
    ApprovalPrompt Button {{
        min-width: 0;
        height: auto;
        padding: 0 1;
        margin-left: 1;
        text-style: bold;
        color: white;
    }}
    ApprovalPrompt #btn-approve-allow {{
        background: #2d6a4f;
        color: #b7e4c7;
    }}
    ApprovalPrompt #btn-approve-allow:hover {{
        background: #40916c;
    }}
    ApprovalPrompt #btn-approve-always {{
        background: #7f5539;
        color: {OCHRE_BRIGHT};
    }}
    ApprovalPrompt #btn-approve-always:hover {{
        background: #9c6644;
    }}
    ApprovalPrompt #btn-approve-deny {{
        background: #6b2c2c;
        color: #f4a5a5;
    }}
    ApprovalPrompt #btn-approve-deny:hover {{
        background: #8b3a3a;
    }}
    """

    def __init__(self, reason: str):
        super().__init__()
        self._reason = reason

    def compose(self) -> ComposeResult:
        yield Static(
            f"[{OCHRE_BRIGHT}]?[/] [{TEXT_SECONDARY}]{escape(self._reason)}[/]",
            classes="approval-label",
        )
        yield Button("Allow", id="btn-approve-allow", flat=True)
        yield Button("Always", id="btn-approve-always", flat=True)
        yield Button("Deny", id="btn-approve-deny", flat=True)


class UserMessage(Static):
    """User input message with distinct background and accent bar."""

    DEFAULT_CSS = f"""
    UserMessage {{
        height: auto;
        padding: 1 1 1 2;
        margin: 1 2 1 2;
        background: {SURFACE_LIGHT};
        border-left: thick {OCHRE_BRIGHT};
        color: {TEXT_PRIMARY};
    }}
    """


class SystemMessage(Static):
    """System/slash-command response."""

    DEFAULT_CSS = f"""
    SystemMessage {{
        height: auto;
        padding: 0 1 0 2;
        margin: 0 2 1 4;
        color: {TEXT_SECONDARY};
        border-left: tall {OCHRE_MUTED};
    }}
    """


class PermissionModeLabel(Static):
    """Persistent label below the input box showing current permission mode."""

    DEFAULT_CSS = f"""
    PermissionModeLabel {{
        dock: bottom;
        height: 1;
        margin: 0 2 0 2;
        padding: 0 1;
        color: {OCHRE_BRIGHT};
        background: transparent;
    }}
    """

    def set_mode(self, mode: str) -> None:
        self.update(f"\u25b6\u25b6  {mode} mode on  [{TEXT_DIM}](shift+tab to cycle)[/]")


class PromptInput(Input):
    BINDINGS = [
        Binding("shift+tab", "app.cycle_permission_mode", "Cycle Mode", show=False),
    ]

    DEFAULT_CSS = f"""
    PromptInput {{
        dock: bottom;
        margin: 0 2 1 2;
        border-top: solid {OCHRE_MUTED};
        border-bottom: solid {OCHRE_MUTED};
        border-left: none;
        border-right: none;
        background: transparent;
        color: {TEXT_PRIMARY};
        padding: 0 1;
    }}
    PromptInput:focus {{
        border-top: solid {OCHRE_PRIMARY};
        border-bottom: solid {OCHRE_PRIMARY};
    }}
    """


# ================================================================
#  Main App
# ================================================================

# Slash commands that involve LLM/network calls and must run in background
_SLOW_SLASH_COMMANDS = {"/plan"}


class FunHarnessApp(App):
    """FunHarness TUI - Mini Claude Code Agent."""

    TITLE = "FunHarness"
    CSS = f"""
    Screen {{
        background: {SURFACE_DARK};
    }}
    #chat-scroll {{
        height: 1fr;
        padding: 0 0 1 0;
        scrollbar-size: 1 1;
        scrollbar-background: {SURFACE_DARK};
        scrollbar-color: {OCHRE_DIM};
    }}
    .turn-cost-label {{
        text-align: right;
        height: auto;
        padding: 0 2;
        margin: 0 2 1 0;
        color: {TEXT_DIM};
    }}
    """

    BINDINGS = [
        Binding("ctrl+c", "quit_app", "Quit", show=True),
        Binding("ctrl+l", "clear_chat", "Clear", show=True),
        Binding("ctrl+z", "interrupt_agent", "Interrupt", show=True),
        Binding("escape", "focus_input", "Focus Input", show=False),
        Binding("shift+tab", "cycle_permission_mode", "Cycle Mode", show=False),
    ]

    def __init__(self):
        super().__init__()
        self._agent = None
        self._current_md_buffer = ""
        self._is_streaming = False
        self._streaming_widget: StreamingText | None = None
        self._thinking_widget: ThinkingIndicator | None = None
        self._reasoning_widget: ReasoningBlock | None = None
        self._tool_gen_widget: ToolGenBlock | None = None
        self._last_tool_block: ToolCallBlock | None = None
        self._last_tool_name: str = ""
        self._pending_tool_blocks: dict[int, ToolCallBlock] = {}
        self._pending_tool_arg_buffers: dict[int, str] = {}
        self._pending_tool_indices_by_name: dict[str, list[int]] = {}
        self._last_stream_render_at = 0.0
        # Permission mode cycling
        self._permission_modes = ["auto", "suggest", "approve"]
        self._current_mode_index = 1  # default: suggest
        # Approval synchronization state
        self._approval_event: threading.Event | None = None
        self._approval_result: dict | None = None
        self._approval_widget: ApprovalPrompt | None = None
        self._agent_busy = False
        self._interrupt_requested = False

    def compose(self) -> ComposeResult:
        yield VerticalScroll(
            BannerWidget(get_full_banner()),
            id="chat-scroll",
        )
        yield StatusBar(id="status-bar")
        yield PromptInput(
            placeholder="> Type a message or /help for commands...",
            id="prompt-input",
        )
        yield PermissionModeLabel(id="mode-label")

    def on_mount(self) -> None:
        self.register_theme(funharness_theme)
        self.theme = "funharness"
        self._init_agent()
        self._update_status()
        # Initialize mode label
        mode_label = self.query_one("#mode-label", PermissionModeLabel)
        mode_label.set_mode(self._permission_modes[self._current_mode_index])
        self.query_one("#prompt-input", PromptInput).focus()

    def _init_agent(self):
        import os
        from ..agent import FunHarnessAgent
        self._agent = FunHarnessAgent(
            mode="suggest",
            on_token=self._on_token_sync,
            on_reasoning_token=self._on_reasoning_token_sync,
            on_reasoning_start=self._on_reasoning_start_sync,
            on_tool_gen=self._on_tool_gen_sync,
            on_tool_call=self._on_tool_call_sync,
            on_tool_result=self._on_tool_result_sync,
            on_status=self._on_status_sync,
            on_approval=self._on_approval_sync,
        )
        info = self._agent.get_info()
        workspace = os.getcwd()
        scroll = self.query_one("#chat-scroll", VerticalScroll)
        scroll.mount(SystemMessage(
            f"[{TEXT_DIM}]  Mode: {info['mode']} | "
            f"Tools: {info['tools']} | "
            f"Trace: {info['trace_id']}\n"
            f"  Workspace: {workspace}[/]"
        ))

    # ---- Thread-safe callbacks ----

    def _safe_callback(self, method, *args):
        """Call a UI method from any thread safely.

        If already on the main (app) thread, call directly.
        If on a worker thread, use call_from_thread.
        """
        if threading.get_ident() == self._thread_id:
            method(*args)
        else:
            self.call_from_thread(method, *args)

    def _on_token_sync(self, token: str):
        self._safe_callback(self._append_token, token)

    def _on_reasoning_token_sync(self, token: str):
        self._safe_callback(self._append_reasoning_token, token)

    def _on_reasoning_start_sync(self):
        self._safe_callback(self._show_reasoning_start)

    def _on_tool_gen_sync(self, index: int, name: str, chunk: str):
        self._safe_callback(self._append_tool_gen, index, name, chunk)

    def _on_tool_call_sync(self, name: str, preview: str, risk: str):
        self._safe_callback(self._show_tool_call, name, preview, risk)

    def _on_tool_result_sync(self, name: str, result: str, hook_feedback: str):
        self._safe_callback(self._show_tool_result, name, result, hook_feedback)

    def _on_status_sync(self, msg: str):
        self._safe_callback(self._show_status, msg)

    def _on_approval_sync(self, tool_name, arguments, reason):
        """Block worker thread and show interactive approval UI.

        Called from worker thread by the permission system.
        Mounts buttons on main thread, then waits for user decision.
        """
        event = threading.Event()
        result = {"approved": False, "choice": ""}
        # Schedule UI update on main thread (blocks until mount completes)
        self.call_from_thread(
            self._show_approval_ui, tool_name, arguments, reason, event, result,
        )
        # Block worker thread until user clicks a button
        event.wait()
        return result["approved"], result["choice"]

    def _show_approval_ui(self, tool_name, arguments, reason, event, result):
        """Mount approval buttons under the current tool call block (main thread)."""
        self._approval_event = event
        self._approval_result = result
        # Mark the tool block as awaiting approval
        if self._last_tool_block is not None:
            self._last_tool_block.set_pending_approval()
        scroll = self.query_one("#chat-scroll", VerticalScroll)
        prompt = ApprovalPrompt(reason)
        scroll.mount(prompt)
        self._approval_widget = prompt
        self._scroll_bottom()

    def _resolve_approval(self, approved: bool, choice: str):
        """Resolve a pending approval and unblock the worker thread."""
        if self._approval_result is not None:
            self._approval_result["approved"] = approved
            self._approval_result["choice"] = choice
        # Update the tool block border
        if self._last_tool_block is not None:
            self._last_tool_block.set_approval_status(approved)
        # Remove approval widget
        if self._approval_widget is not None:
            self._approval_widget.remove()
            self._approval_widget = None
        # Unblock the worker thread
        if self._approval_event is not None:
            self._approval_event.set()
            self._approval_event = None
            self._approval_result = None

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle approval button clicks."""
        if self._approval_event is None:
            return
        bid = event.button.id
        if bid == "btn-approve-allow":
            self._resolve_approval(True, "once")
        elif bid == "btn-approve-always":
            self._resolve_approval(True, "always")
        elif bid == "btn-approve-deny":
            self._resolve_approval(False, "")

    # ---- Thinking animation ----

    def _show_thinking(self):
        if self._thinking_widget is None:
            self._thinking_widget = ThinkingIndicator()
            scroll = self.query_one("#chat-scroll", VerticalScroll)
            scroll.mount(self._thinking_widget)
            self._scroll_bottom()

    def _hide_thinking(self):
        if self._thinking_widget is not None:
            self._thinking_widget.remove()
            self._thinking_widget = None

    # ---- Reasoning (thinking) display ----

    def _show_reasoning_start(self):
        """Called when a new reasoning stream begins."""
        self._hide_thinking()
        if self._reasoning_widget is None:
            self._reasoning_widget = ReasoningBlock()
            scroll = self.query_one("#chat-scroll", VerticalScroll)
            scroll.mount(self._reasoning_widget)
            self._scroll_bottom()

    def _append_reasoning_token(self, token: str):
        """Append a reasoning token to the reasoning block."""
        self._hide_thinking()
        if self._reasoning_widget is None:
            self._show_reasoning_start()
        if self._reasoning_widget.append_token(token):
            self._scroll_bottom()

    def _finish_reasoning(self):
        """Mark the reasoning block as done (collapsible)."""
        if self._reasoning_widget is not None:
            self._reasoning_widget.finish_streaming()
            self._reasoning_widget = None

    # ---- Tool generation streaming ----

    def _append_tool_gen(self, index: int, name: str, chunk: str):
        """Show long-running write/replace tools as soon as arguments stream."""
        early_tools = {"tool_write_file", "tool_replace_in_file"}
        if name not in early_tools:
            return
        self._finish_reasoning()
        self._hide_thinking()

        buffer = self._pending_tool_arg_buffers.get(index, "") + chunk
        self._pending_tool_arg_buffers[index] = buffer
        call_markup = _format_tool_args_preview(buffer)

        indices = self._pending_tool_indices_by_name.setdefault(name, [])
        if index not in indices:
            indices.append(index)

        block = self._pending_tool_blocks.get(index)
        if block is None:
            risk = RISK_CONFIG.get("write", RISK_CONFIG["write"])["label"]
            block = ToolCallBlock(call_markup, tool_name=name, risk_label=risk)
            self._pending_tool_blocks[index] = block
            scroll = self.query_one("#chat-scroll", VerticalScroll)
            scroll.mount(block)
            self._last_tool_block = block
            self._last_tool_name = name
            self._scroll_bottom()
            return

        block.update_call_markup(call_markup)
        self._last_tool_block = block
        self._last_tool_name = name
        self._scroll_bottom()

    def _finish_tool_gen(self):
        """Remove the tool generation preview."""
        if self._tool_gen_widget is not None:
            self._tool_gen_widget.remove()
            self._tool_gen_widget = None

    # ---- Streaming ----

    def _append_token(self, token: str):
        # Finish reasoning/tool-gen block on first content token
        self._finish_reasoning()
        self._finish_tool_gen()
        # Remove thinking indicator on first token
        self._hide_thinking()

        scroll = self.query_one("#chat-scroll", VerticalScroll)
        if not self._is_streaming:
            self._is_streaming = True
            self._current_md_buffer = ""
            self._streaming_widget = StreamingText("")
            scroll.mount(self._streaming_widget)

        self._current_md_buffer += token
        did_render = False
        if self._streaming_widget is not None:
            now = time.monotonic()
            if "\n" in token or now - self._last_stream_render_at >= STREAM_RENDER_INTERVAL:
                self._last_stream_render_at = now
                self._streaming_widget.update(escape(self._current_md_buffer))
                did_render = True
        if did_render:
            self._scroll_bottom()

    def _finish_stream(self):
        if self._is_streaming:
            self._is_streaming = False
            if self._streaming_widget is not None:
                self._streaming_widget.remove()
                self._streaming_widget = None
            if self._current_md_buffer.strip():
                scroll = self.query_one("#chat-scroll", VerticalScroll)
                scroll.mount(AssistantMessage(self._current_md_buffer))
            self._current_md_buffer = ""

    # ---- Tool call display ----

    def _show_tool_call(self, name: str, preview: str, risk: str):
        # Finish any ongoing reasoning/tool-gen/stream first
        self._finish_reasoning()
        self._finish_tool_gen()
        if self._is_streaming:
            self._finish_stream()
        self._hide_thinking()

        # Get risk styling
        rc = RISK_CONFIG.get(risk, RISK_CONFIG["execute"])
        risk_label = rc["label"]

        call_markup = _format_tool_args_preview(preview)

        existing = None
        indices = self._pending_tool_indices_by_name.get(name, [])
        while indices:
            pending_index = indices.pop(0)
            existing = self._pending_tool_blocks.pop(pending_index, None)
            self._pending_tool_arg_buffers.pop(pending_index, None)
            if existing is not None:
                break
        if not indices:
            self._pending_tool_indices_by_name.pop(name, None)
        if existing is not None:
            existing.update_call_markup(call_markup)
            self._last_tool_block = existing
            self._last_tool_name = name
            self._scroll_bottom()
            return

        scroll = self.query_one("#chat-scroll", VerticalScroll)
        block = ToolCallBlock(call_markup, tool_name=name, risk_label=risk_label)
        scroll.mount(block)
        self._last_tool_block = block
        self._last_tool_name = name
        self._scroll_bottom()

    def _show_tool_result(self, name: str, result: str, hook_feedback: str):
        if len(result) > 300:
            display = escape(result[:300]) + f"\n[{TEXT_DIM}]...(truncated, {len(result)} chars total)[/]"
        else:
            display = escape(result)

        # Color the result based on success/error
        has_error = any(kw in result for kw in ["Error", "Failed", "DENIED"])
        if has_error:
            result_icon = ICONS["error"]
            result_color = ERROR_COLOR
        else:
            result_icon = ICONS["result"]
            result_color = SUCCESS_COLOR

        result_markup = (
            f"[{result_color}]{result_icon}[/] [{TEXT_SECONDARY}]{display}[/]"
        )

        hook_markup = ""
        if hook_feedback:
            hook_markup = f"[{OCHRE_DIM}][hook] {escape(hook_feedback)}[/]"

        if self._last_tool_block is not None:
            self._last_tool_block.add_result(result_markup, hook_markup)
        else:
            # Fallback: create standalone result block
            scroll = self.query_one("#chat-scroll", VerticalScroll)
            scroll.mount(SystemMessage(result_markup))

        # For tool_write_file, show a file content preview
        if self._last_tool_name == "tool_write_file" and not has_error:
            self._mount_file_preview(result)

        self._scroll_bottom()

    def _mount_file_preview(self, result: str):
        """Mount a FilePreviewBlock for a tool_write_file result."""
        import re as _re
        from pathlib import Path as _Path
        # Parse path from result like "Written to path (N chars, M lines)"
        m = _re.match(r"Written to (.+?) \(", result)
        if not m:
            return
        filepath = m.group(1).strip()
        try:
            content = _Path(filepath).read_text(encoding="utf-8")
        except Exception:
            return
        if not content.strip():
            return
        scroll = self.query_one("#chat-scroll", VerticalScroll)
        scroll.mount(FilePreviewBlock(filepath, content))

    def _show_status(self, msg: str):
        scroll = self.query_one("#chat-scroll", VerticalScroll)
        scroll.mount(SystemMessage(
            f"[{TEXT_DIM}][status] {escape(msg)}[/]"
        ))
        self._scroll_bottom()

    def _show_user_message(self, text: str):
        scroll = self.query_one("#chat-scroll", VerticalScroll)
        scroll.mount(UserMessage(
            f"[bold {OCHRE_BRIGHT}]{ICONS['agent']}[/] "
            f"[bold {TEXT_PRIMARY}]{escape(text)}[/]"
        ))
        self._scroll_bottom()

    def _show_system_response(self, text: str):
        scroll = self.query_one("#chat-scroll", VerticalScroll)
        scroll.mount(SystemMessage(
            f"[{TEXT_SECONDARY}]{escape(text)}[/]"
        ))
        self._scroll_bottom()

    def _scroll_bottom(self):
        scroll = self.query_one("#chat-scroll", VerticalScroll)
        scroll.scroll_end(animate=False)

    def _update_status(self):
        if self._agent:
            info = self._agent.get_info()
            status = self.query_one("#status-bar", StatusBar)
            cost_parts = info["cost"].split("Cost: ")
            cost_str = cost_parts[-1] if len(cost_parts) > 1 else "$0.0000"
            status.update_info(
                mode=info["mode"], messages=info["messages"],
                tokens=info["tokens"], cost=cost_str,
                team=info.get("teammates", 0),
                runtime=info.get("runtime_tasks", 0),
                schedules=info.get("schedules", 0),
            )

    # ---- Event Handlers ----

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        user_input = event.value.strip()
        if not user_input:
            return

        input_widget = self.query_one("#prompt-input", PromptInput)
        input_widget.value = ""

        if user_input.lower() == "quit":
            if self._agent:
                self._agent._save_session()
                if self._agent.dashboard._records:
                    self._show_system_response(self._agent.dashboard.report())
            self._show_system_response("Bye!")
            self.set_timer(0.5, self.exit)
            return

        if user_input.lower() == "clear":
            if self._agent:
                self._agent.clear()
            scroll = self.query_one("#chat-scroll", VerticalScroll)
            for child in list(scroll.children):
                if not isinstance(child, BannerWidget):
                    child.remove()
            self._show_system_response("[conversation cleared]")
            self._update_status()
            return

        if user_input.startswith("/") and self._agent:
            self._show_user_message(user_input)
            # Commands that involve LLM/network calls must run in background
            cmd = user_input.strip().split(maxsplit=1)[0].lower()
            if cmd in _SLOW_SLASH_COMMANDS:
                input_widget.disabled = True
                self._show_thinking()
                self._run_slow_slash(user_input)
                return
            result = self._agent.handle_slash_command(user_input)
            if result is not None:
                self._show_system_response(result)
                self._update_status()
                return

        # Regular message -> agent
        if self._agent_busy:
            self._show_system_response("Agent is still stopping. Please wait a moment.")
            return
        self._show_user_message(user_input)
        input_widget.disabled = True
        self._agent_busy = True
        self._interrupt_requested = False
        self._show_thinking()
        self._run_agent(user_input)

    @work(thread=True)
    def _run_agent(self, user_input: str) -> None:
        try:
            self._agent.run(user_input)
        except InterruptedError:
            self.call_from_thread(self._show_status, "Interrupted by user.")
        except Exception as e:
            self.call_from_thread(self._show_system_response, f"[Error] {e}")
        finally:
            self.call_from_thread(self._after_agent_run)

    @work(thread=True)
    def _run_slow_slash(self, user_input: str) -> None:
        """Run a slow slash command in a background thread."""
        try:
            result = self._agent.handle_slash_command(user_input)
            if result is not None:
                self.call_from_thread(self._show_system_response, result)
        except Exception as e:
            self.call_from_thread(self._show_system_response, f"[Error] {e}")
        finally:
            self.call_from_thread(self._after_slow_slash)

    def _after_slow_slash(self):
        """Cleanup after a slow slash command finishes."""
        self._hide_thinking()
        self._update_status()
        input_widget = self.query_one("#prompt-input", PromptInput)
        input_widget.disabled = False
        input_widget.focus()

    def _after_agent_run(self):
        self._agent_busy = False
        self._hide_thinking()
        self._finish_reasoning()
        self._finish_tool_gen()
        self._finish_stream()

        # Show per-turn token/cost in a right-aligned dim label
        if self._agent and self._agent.cost_tracker.turn_tokens > 0:
            turn_text = self._agent.cost_tracker.turn_summary()
            scroll = self.query_one("#chat-scroll", VerticalScroll)
            scroll.mount(Static(
                f"[{TEXT_DIM}]{turn_text}[/]",
                classes="turn-cost-label",
            ))
            self._scroll_bottom()

        self._update_status()
        self._last_tool_block = None
        self._pending_tool_blocks.clear()
        self._pending_tool_arg_buffers.clear()
        self._pending_tool_indices_by_name.clear()
        input_widget = self.query_one("#prompt-input", PromptInput)
        input_widget.disabled = False
        input_widget.focus()

    # ---- Actions ----

    def action_quit_app(self) -> None:
        if self._agent:
            self._agent._save_session()
        self.exit()

    def action_clear_chat(self) -> None:
        if self._agent:
            self._agent.clear()
        scroll = self.query_one("#chat-scroll", VerticalScroll)
        for child in list(scroll.children):
            if not isinstance(child, BannerWidget):
                child.remove()
        self._update_status()

    def action_focus_input(self) -> None:
        self.query_one("#prompt-input", PromptInput).focus()

    def action_interrupt_agent(self) -> None:
        """Interrupt the current agent run and any interruptible tool."""
        if not self._agent_busy or self._interrupt_requested:
            return
        self._interrupt_requested = True
        if self._agent:
            self._agent.request_interrupt()

        if self._approval_event is not None:
            self._resolve_approval(False, "")

        self._hide_thinking()
        self._finish_reasoning()
        self._finish_tool_gen()
        if self._last_tool_block is not None:
            self._last_tool_block.add_result(
                f"[{ERROR_COLOR}]{ICONS['warning']}[/] "
                f"[{TEXT_SECONDARY}]Interrupted by user[/]"
            )
        else:
            self._show_status("Interrupt requested.")

        input_widget = self.query_one("#prompt-input", PromptInput)
        input_widget.disabled = False
        input_widget.focus()

    def action_cycle_permission_mode(self) -> None:
        """Cycle through permission modes: auto -> suggest -> approve -> auto."""
        self._current_mode_index = (
            self._current_mode_index + 1
        ) % len(self._permission_modes)
        new_mode = self._permission_modes[self._current_mode_index]
        # Update agent mode
        if self._agent:
            result = self._agent.handle_slash_command(f"/mode {new_mode}")
        # Update mode label
        mode_label = self.query_one("#mode-label", PermissionModeLabel)
        mode_label.set_mode(new_mode)
        # Update status bar
        self._update_status()
