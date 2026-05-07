import unittest
from types import SimpleNamespace

from funharness.src.core.llm import call_with_retry, process_stream_response, sanitize_messages_for_api


class Usage:
    def model_dump(self, exclude_none=True):
        return {
            "prompt_tokens": 25,
            "completion_tokens": 659,
            "total_tokens": 684,
            "completion_tokens_details": {"reasoning_tokens": 147},
        }


class CostTracker:
    def __init__(self):
        self.usages = []

    def update(self, usage):
        self.usages.append(usage)


class FakeCompletions:
    def __init__(self):
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return kwargs


class FakeClient:
    def __init__(self):
        self.completions = FakeCompletions()
        self.chat = SimpleNamespace(completions=self.completions)


class LlmMessageTests(unittest.TestCase):
    def test_process_stream_response_preserves_response_metadata(self):
        chunks = [
            SimpleNamespace(
                id="3a56ed34-7cdb-44ad-ab29-92edff14909c",
                object="chat.completion.chunk",
                created=1777913055,
                model="deepseek-v4-pro",
                system_fingerprint="fp_9954b31ca7_prod0820_fp8_kvcache_20260402",
                choices=[
                    SimpleNamespace(
                        index=0,
                        finish_reason=None,
                        logprobs=None,
                        delta=SimpleNamespace(reasoning_content="thinking", content=None, tool_calls=None),
                    )
                ],
                usage=None,
            ),
            SimpleNamespace(
                id="3a56ed34-7cdb-44ad-ab29-92edff14909c",
                object="chat.completion.chunk",
                created=1777913055,
                model="deepseek-v4-pro",
                system_fingerprint="fp_9954b31ca7_prod0820_fp8_kvcache_20260402",
                choices=[
                    SimpleNamespace(
                        index=0,
                        finish_reason="stop",
                        logprobs=None,
                        delta=SimpleNamespace(reasoning_content=None, content="answer", tool_calls=None),
                    )
                ],
                usage=None,
            ),
            SimpleNamespace(
                id="3a56ed34-7cdb-44ad-ab29-92edff14909c",
                object="chat.completion.chunk",
                created=1777913055,
                model="deepseek-v4-pro",
                system_fingerprint="fp_9954b31ca7_prod0820_fp8_kvcache_20260402",
                choices=[],
                usage=Usage(),
            ),
        ]
        cost_tracker = CostTracker()

        message = process_stream_response(chunks, cost_tracker=cost_tracker)

        self.assertEqual(message["role"], "assistant")
        self.assertEqual(message["content"], "answer")
        self.assertEqual(message["reasoning_content"], "thinking")
        metadata = message["response_metadata"]
        self.assertEqual(metadata["id"], "3a56ed34-7cdb-44ad-ab29-92edff14909c")
        self.assertEqual(metadata["model"], "deepseek-v4-pro")
        self.assertEqual(metadata["finish_reason"], "stop")
        self.assertEqual(metadata["usage"]["completion_tokens_details"]["reasoning_tokens"], 147)
        self.assertEqual(len(cost_tracker.usages), 1)

    def test_sanitize_messages_for_api_strips_session_only_fields(self):
        messages = [
            {"role": "system", "content": "system", "response_metadata": {"id": "ignored"}},
            {
                "role": "assistant",
                "content": "hello",
                "reasoning_content": "think",
                "response_metadata": {"id": "cmpl_1"},
                "created_at": "2026-05-05T00:00:00",
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "ok", "response_metadata": {}},
        ]

        self.assertEqual(
            sanitize_messages_for_api(messages),
            [
                {"role": "system", "content": "system"},
                {"role": "assistant", "content": "hello", "reasoning_content": "think"},
                {"role": "tool", "content": "ok", "tool_call_id": "call_1"},
            ],
        )

    def test_call_with_retry_uses_sanitized_messages_and_requests_stream_usage(self):
        fake_client = FakeClient()
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello", "response_metadata": {"id": "cmpl_1"}},
        ]

        call_with_retry(messages, tools=[], stream=True, model="test-model", llm_client=fake_client)

        kwargs = fake_client.completions.kwargs
        self.assertEqual(kwargs["messages"], [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ])
        self.assertEqual(kwargs["stream_options"], {"include_usage": True})


if __name__ == "__main__":
    unittest.main()
