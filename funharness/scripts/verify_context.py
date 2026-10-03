"""Opt-in real DeepSeek context recovery smoke test (billable API calls).

The overflow response is injected locally; oversized history is never sent to
the provider. The bounded summary, resumed inference, and reload are real calls.
Run: python -m funharness.scripts.verify_context [--key-stdin]
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import tempfile
from unittest.mock import patch

from openai import OpenAI

from funharness.src.core.context import ContextManager, estimate_tokens
from funharness.src.core.llm import call_with_retry, process_stream_response
from funharness.src.core.session import Session, SessionManager


def history(code):
    return [
        {"role": "system", "content": "Follow the latest user request. Respond in one short English sentence."},
        {"role": "user", "content": f"The project code is {code}. Remember it. The final reply must contain that exact code and the word recovered."},
        {"role": "assistant", "content": "I recorded the project code."},
        {"role": "user", "context_kind": "background", "content": "Historical build log: " + "build completed\n" * 8000},
        {"role": "assistant", "content": "The build completed successfully."},
        {"role": "user", "content": "Check progress."},
        {"role": "assistant", "content": "No further build steps remain."},
        {"role": "user", "content": "Continue and give the final reply with the original project code."},
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key-stdin", action="store_true")
    args = parser.parse_args()
    api_key = sys.stdin.readline().strip() if args.key_stdin else os.getenv("DEEPSEEK_API_KEY", "")
    if not api_key:
        raise SystemExit("DEEPSEEK_API_KEY or --key-stdin is required")

    with OpenAI(api_key=api_key, base_url="https://api.deepseek.com", timeout=90, max_retries=1) as client:
        with tempfile.TemporaryDirectory(prefix="funharness-context-") as tmp:
            for case in ("proactive_compaction", "injected_overflow_recovery"):
                code = secrets.token_hex(4).upper()
                messages = history(code)
                state = {}
                # Proactive case crosses a deliberately small *local* window.
                window = "16000" if case == "proactive_compaction" else "1048576"
                with patch.dict(os.environ, {"FUNHARNESS_CONTEXT_WINDOW": window, "FUNHARNESS_MAX_OUTPUT_TOKENS": "2048"}):
                    manager = ContextManager("deepseek-flash", client, state)
                    session = Session(messages=messages, context_state=state)
                    store = SessionManager(tmp)
                    attempts = []

                    def invoke(max_tokens):
                        attempts.append(estimate_tokens(messages))
                        if case == "injected_overflow_recovery" and len(attempts) == 1:
                            raise ValueError("context_length_exceeded: maximum context length is 8192 tokens (locally injected test error)")
                        stream = call_with_retry(messages, [], stream=True, model="deepseek-flash", llm_client=client, max_tokens=max_tokens)
                        try:
                            return process_stream_response(stream)
                        finally:
                            stream.close()

                    response = manager.run(messages, [], invoke,
                        before_compact=lambda original: store.archive_context(session, original),
                        after_compact=lambda: store.save(session))
                    answer = response.get("content") or ""
                    assert code in answer.upper() and "recovered" in answer.lower(), "Original goal was not retained"
                    messages.append(response)
                    store.save(session)
                    loaded = store.load(session.id)
                    restored = ContextManager("deepseek-flash", client, loaded.context_state)
                    assert restored.input_budget == manager.input_budget
                    assert loaded.messages == messages
                    print(json.dumps({"case": case, "passed": True, "overflow_is_simulated": case.startswith("injected"),
                        "request_estimates": attempts, "saved_input_budget": restored.input_budget,
                        "usage": response.get("response_metadata", {}).get("usage", {})}, ensure_ascii=False), flush=True)
                    if case == "injected_overflow_recovery":
                        messages[:] = loaded.messages
                        messages.append({"role": "user", "content": "Continue: repeat the final project code and recovered status."})
                        response = restored.run(messages, [], invoke)
                        assert code in (response.get("content") or "").upper()
                        print(json.dumps({"case": "continue_after_reload", "passed": True}), flush=True)


if __name__ == "__main__":
    main()
