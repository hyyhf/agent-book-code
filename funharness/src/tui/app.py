"""
FunHarness - TUI Application

Textual-based terminal UI inspired by Claude Code.
Features: bordered tool calls, streaming with accent, thinking animation.
"""
from __future__ import annotations
import threading

from rich.markup import escape

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import Footer, Input, Static
from textual.timer import Timer

from .banner import get_full_banner
from .theme import (
    funharness_theme, ICONS, SPINNER_FRAMES, RISK_CONFIG,
    OCHRE_PRIMARY, OCHRE_BRIGHT, OCHRE_DIM, OCHRE_MUTED,
    SURFACE_DARK, SURFACE_LIGHT, PANEL_BG,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_DIM,
    SUCCESS_COLOR, ERROR_COLOR, INFO_COLOR,
)


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
        background: {SURFACE_DARK};
        color: {TEXT_SECONDARY};
        padding: 0 2;
    }}
    """

    def update_info(self, mode="suggest", messages=0, tokens=0, cost="$0.0000"):
        self.update(
            f" [{OCHRE_PRIMARY}]FunHarness[/] | "
            f"Mode: [bold {OCHRE_BRIGHT}]{mode}[/] | "
            f"Msgs: {messages} | "
            f"~{tokens:,} tok | "
            f"Cost: {cost}"
        )


class ThinkingIndicator(Static):
    """Animated spinner while waiting for LLM response."""

    DEFAULT_CSS = f"""
    ThinkingIndicator {{
        height: 1;
        padding: 0 2;
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

    def on_mount(self) -> None:
        self._timer = self.set_interval(0.12, self._tick_spinner)

    def _tick_spinner(self) -> None:
        if self._is_streaming:
            self._frame_idx = (self._frame_idx + 1) % len(SPINNER_FRAMES)
            self._render_display()

    def append_token(self, token: str) -> None:
        self._reasoning_buffer += token
        self._render_display()

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
                lines.append(f"[{TEXT_DIM}]{'\n'.join(display_lines)}[/]")
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
    """Finalized assistant response with left accent border."""

    DEFAULT_CSS = f"""
    AssistantMessage {{
        height: auto;
        padding: 0 1 0 2;
        margin: 0 2 1 4;
        border-left: thick {OCHRE_PRIMARY};
        color: {TEXT_PRIMARY};
    }}
    """


class ToolCallBlock(Static):
    """Bordered box showing a tool invocation and its result.

    Created with call info, then updated with result via add_result().
    """

    DEFAULT_CSS = f"""
    ToolCallBlock {{
        height: auto;
        padding: 1 2;
        margin: 0 2 1 4;
        border: round {OCHRE_MUTED};
        background: {PANEL_BG};
    }}
    """

    def __init__(self, call_markup: str, **kwargs):
        super().__init__(call_markup, **kwargs)
        self._call_markup = call_markup
        self._result_markup = ""
        self._hook_markup = ""

    def add_result(self, result_markup: str, hook_markup: str = ""):
        self._result_markup = result_markup
        self._hook_markup = hook_markup
        parts = [self._call_markup]
        parts.append(f"[{OCHRE_MUTED}]{'~' * 50}[/]")
        parts.append(self._result_markup)
        if self._hook_markup:
            parts.append(self._hook_markup)
        self.update("\n".join(parts))


class UserMessage(Static):
    """User input message."""

    DEFAULT_CSS = f"""
    UserMessage {{
        height: auto;
        padding: 0 1;
        margin: 1 2 0 2;
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
    }}
    """


class PromptInput(Input):
    DEFAULT_CSS = f"""
    PromptInput {{
        dock: bottom;
        margin: 0 1;
        border: tall {OCHRE_MUTED};
        background: {PANEL_BG};
        color: {TEXT_PRIMARY};
    }}
    PromptInput:focus {{
        border: tall {OCHRE_PRIMARY};
    }}
    """


# ================================================================
#  Main App
# ================================================================

class FunHarnessApp(App):
    """FunHarness TUI - Mini Claude Code Agent."""

    TITLE = "FunHarness"
    CSS = f"""
    Screen {{
        background: {SURFACE_DARK};
    }}
    #chat-scroll {{
        height: 1fr;
        scrollbar-size: 1 1;
    }}
    """

    BINDINGS = [
        Binding("ctrl+c", "quit_app", "Quit", show=True),
        Binding("ctrl+l", "clear_chat", "Clear", show=True),
        Binding("escape", "focus_input", "Focus Input", show=False),
    ]

    def __init__(self):
        super().__init__()
        self._agent = None
        self._current_md_buffer = ""
        self._is_streaming = False
        self._streaming_widget: StreamingText | None = None
        self._thinking_widget: ThinkingIndicator | None = None
        self._reasoning_widget: ReasoningBlock | None = None
        self._last_tool_block: ToolCallBlock | None = None

    def compose(self) -> ComposeResult:
        yield VerticalScroll(
            BannerWidget(get_full_banner()),
            id="chat-scroll",
        )
        yield StatusBar(id="status-bar")
        yield PromptInput(
            placeholder="  Type a message or /help for commands...",
            id="prompt-input",
        )
        yield Footer()

    def on_mount(self) -> None:
        self.register_theme(funharness_theme)
        self.theme = "funharness"
        self._init_agent()
        self._update_status()
        self.query_one("#prompt-input", PromptInput).focus()

    def _init_agent(self):
        from ..agent import FunHarnessAgent
        self._agent = FunHarnessAgent(
            mode="suggest",
            on_token=self._on_token_sync,
            on_reasoning_token=self._on_reasoning_token_sync,
            on_reasoning_start=self._on_reasoning_start_sync,
            on_tool_call=self._on_tool_call_sync,
            on_tool_result=self._on_tool_result_sync,
            on_status=self._on_status_sync,
            on_approval=self._on_approval_sync,
        )
        info = self._agent.get_info()
        scroll = self.query_one("#chat-scroll", VerticalScroll)
        scroll.mount(SystemMessage(
            f"[{TEXT_DIM}]  Mode: {info['mode']} | "
            f"Tools: {info['tools']} | "
            f"Trace: {info['trace_id']}[/]"
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

    def _on_tool_call_sync(self, name: str, preview: str, risk: str):
        self._safe_callback(self._show_tool_call, name, preview, risk)

    def _on_tool_result_sync(self, name: str, result: str, hook_feedback: str):
        self._safe_callback(self._show_tool_result, name, result, hook_feedback)

    def _on_status_sync(self, msg: str):
        self._safe_callback(self._show_status, msg)

    def _on_approval_sync(self, tool_name, arguments, reason):
        return True, "once"

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
        self._reasoning_widget.append_token(token)
        self._scroll_bottom()

    def _finish_reasoning(self):
        """Mark the reasoning block as done (collapsible)."""
        if self._reasoning_widget is not None:
            self._reasoning_widget.finish_streaming()
            self._reasoning_widget = None

    # ---- Streaming ----

    def _append_token(self, token: str):
        # Finish reasoning block on first content token
        self._finish_reasoning()
        # Remove thinking indicator on first token
        self._hide_thinking()

        scroll = self.query_one("#chat-scroll", VerticalScroll)
        if not self._is_streaming:
            self._is_streaming = True
            self._current_md_buffer = ""
            self._streaming_widget = StreamingText("")
            scroll.mount(self._streaming_widget)

        self._current_md_buffer += token
        if self._streaming_widget is not None:
            self._streaming_widget.update(escape(self._current_md_buffer))
        self._scroll_bottom()

    def _finish_stream(self):
        if self._is_streaming:
            self._is_streaming = False
            if self._streaming_widget is not None:
                self._streaming_widget.remove()
                self._streaming_widget = None
            if self._current_md_buffer.strip():
                scroll = self.query_one("#chat-scroll", VerticalScroll)
                scroll.mount(AssistantMessage(
                    f"[{TEXT_PRIMARY}]{escape(self._current_md_buffer)}[/]"
                ))
            self._current_md_buffer = ""

    # ---- Tool call display ----

    def _show_tool_call(self, name: str, preview: str, risk: str):
        # Finish any ongoing reasoning/stream first
        self._finish_reasoning()
        if self._is_streaming:
            self._finish_stream()
        self._hide_thinking()

        # Get risk styling
        rc = RISK_CONFIG.get(risk, RISK_CONFIG["execute"])
        risk_color = rc["color"]
        risk_label = rc["label"]

        if len(preview) > 100:
            preview = preview[:100] + "..."

        call_markup = (
            f"[{risk_color}][{risk_label}][/] "
            f"[bold {OCHRE_BRIGHT}]{ICONS['tool']}[/] "
            f"[bold {TEXT_PRIMARY}]{name}[/]\n"
            f"[{TEXT_DIM}]{escape(preview)}[/]"
        )

        scroll = self.query_one("#chat-scroll", VerticalScroll)
        block = ToolCallBlock(call_markup)
        scroll.mount(block)
        self._last_tool_block = block
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

        self._scroll_bottom()

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
            result = self._agent.handle_slash_command(user_input)
            if result is not None:
                self._show_system_response(result)
                self._update_status()
                return

        # Regular message -> agent
        self._show_user_message(user_input)
        input_widget.disabled = True
        self._show_thinking()
        self._run_agent(user_input)

    @work(thread=True)
    def _run_agent(self, user_input: str) -> None:
        try:
            self._agent.run(user_input)
        except Exception as e:
            self.call_from_thread(self._show_system_response, f"[Error] {e}")
        finally:
            self.call_from_thread(self._after_agent_run)

    def _after_agent_run(self):
        self._hide_thinking()
        self._finish_reasoning()
        self._finish_stream()
        self._update_status()
        self._last_tool_block = None
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
