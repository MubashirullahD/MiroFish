"""Exercise the real HTTP translation boundary without spending subscription usage."""

import json
import threading

import httpx
import pytest
from flask import Flask

from app import subscription_proxy as proxy


KEY = "local-test-proxy-key"
TOKEN = "private-subscription-token"
HEADERS = {"Authorization": "Bearer " + KEY}
PATH = "/api/chatgpt/v1/chat/completions"


def completion(output=None):
    return {
        "id": "resp_test", "status": "completed", "created_at": 123,
        "model": "account-model",
        "output": output if output is not None else [
            {"type": "message", "content": [{"type": "output_text", "text": "Hello"}]}
        ],
        "usage": {"input_tokens": 11, "output_tokens": 5, "total_tokens": 16},
    }


def sse(*events):
    return "".join("data: " + json.dumps(event) + "\n\n" for event in events)


def completed_response(output=None):
    return httpx.Response(200, text=sse({"type": "response.completed", "response": completion(output)}), headers={"content-type": "text/event-stream", "x-request-id": "req_test"})


@pytest.fixture
def app(monkeypatch):
    app = Flask(__name__)
    app.config.update(TESTING=True, LLM_API_KEY=KEY, LLM_MODEL_NAME="chatgpt-plan", CHATGPT_MAX_REQUESTS=3, CHATGPT_MAX_CONCURRENT=1, CHATGPT_QUEUE_TIMEOUT=0)
    app.register_blueprint(proxy.chatgpt_proxy_bp)
    monkeypatch.setattr(proxy, "get_access_token", lambda: TOKEN)
    monkeypatch.setattr(proxy, "get_selected_model", lambda requested: "account-model" if requested == "chatgpt-plan" else requested)
    monkeypatch.setattr(proxy, "get_account_cache_scope", lambda: "account-login-scope")
    monkeypatch.setattr(proxy, "_tool_context_cache", proxy._ToolContextCache())
    return app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def transport(monkeypatch):
    def install(handler):
        monkeypatch.setattr(proxy, "_http_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    return install


def post(client, **kwargs):
    body = {"model": "chatgpt-plan", "messages": [{"role": "user", "content": "Hello"}], **kwargs}
    return client.post(PATH, json=body, headers=HEADERS)


def test_text_request_obeys_subscription_constraints_and_converts_usage(client, transport):
    captured = []

    def upstream(request):
        captured.append(json.loads(request.content))
        assert str(request.url) == proxy.RESPONSES_URL
        assert request.headers["Authorization"] == "Bearer " + TOKEN
        return completed_response()

    transport(upstream)
    response = post(client, messages=[{"role": "system", "content": "Be concise"}, {"role": "user", "content": [{"type": "text", "text": "Hello"}]}], temperature=0.4, top_p=1, max_tokens=100, max_completion_tokens=100)
    assert response.status_code == 200
    assert captured == [{"model": "account-model", "input": [{"role": "developer", "content": "Be concise"}, {"role": "user", "content": "Hello"}], "store": False, "stream": True, "include": ["reasoning.encrypted_content"]}]
    assert response.json["choices"][0]["message"]["content"] == "Hello"
    assert response.json["usage"] == {"prompt_tokens": 11, "completion_tokens": 5, "total_tokens": 16}
    assert response.headers["x-request-id"] == "req_test"


def test_tool_calls_and_results_survive_a_full_roundtrip(client, transport):
    captured = []
    output = [{"type": "function_call", "namespace": "mirofish", "name": "CREATE_POST", "call_id": "call_post", "arguments": '{"content":"A new feature"}'}]

    def upstream(request):
        captured.append(json.loads(request.content))
        return completed_response(output) if len(captured) == 1 else completed_response()

    transport(upstream)
    tools = [{"type": "function", "function": {"name": "CREATE_POST", "description": "Create a post", "parameters": {"type": "object", "properties": {"content": {"type": "string"}}, "required": ["content"]}}}]
    first = post(client, tools=tools, tool_choice={"type": "function", "function": {"name": "CREATE_POST"}}, parallel_tool_calls=False)
    assert first.status_code == 200
    choice = first.json["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    assert choice["message"]["content"] is None
    assert choice["message"]["tool_calls"] == [{"id": "call_post", "type": "function", "function": {"name": "CREATE_POST", "arguments": '{"content":"A new feature"}'}}]
    second = post(client, tools=tools, messages=[{"role": "user", "content": "Create a post"}, choice["message"], {"role": "tool", "tool_call_id": "call_post", "content": '{"post_id":123}'}])
    assert second.status_code == 200
    assert captured[0]["tools"][0]["type"] == "namespace"
    assert captured[0]["tools"][0]["name"] == "mirofish"
    assert captured[0]["tools"][0]["tools"][0]["strict"] is False
    assert captured[0]["tool_choice"] == {"type": "function", "namespace": "mirofish", "name": "CREATE_POST"}
    assert captured[1]["input"][-2:] == [
        {"type": "function_call", "call_id": "call_post", "name": "CREATE_POST", "namespace": "mirofish", "arguments": '{"content":"A new feature"}'},
        {"type": "function_call_output", "call_id": "call_post", "output": '{"post_id":123}'},
    ]


def test_installed_camel_and_openai_client_can_use_the_adapter(app, transport):
    from camel.models import ModelFactory
    from camel.types import ModelPlatformType
    from openai import OpenAI

    captured = []
    original_output = _tool_output("call_like", encrypted="private-native-camel-reasoning")

    def upstream(request):
        captured.append(json.loads(request.content))
        return completed_response(original_output) if len(captured) == 1 else completed_response()

    transport(upstream)
    model = ModelFactory.create(model_platform=ModelPlatformType.OPENAI, model_type="chatgpt-plan", api_key=KEY, url="http://localhost:5001/api/chatgpt/v1", max_retries=0)
    model._client = OpenAI(api_key=KEY, base_url="http://localhost:5001/api/chatgpt/v1", max_retries=0, http_client=httpx.Client(transport=httpx.WSGITransport(app=app)))
    result = model.run([{"role": "user", "content": "Like post 3"}], tools=[{"type": "function", "function": {"name": "LIKE_POST", "parameters": {"type": "object", "properties": {"post_id": {"type": "integer"}}, "required": ["post_id"]}}}])
    assert captured[0]["model"] == "account-model"
    assert captured[0]["tools"][0]["tools"][0]["name"] == "LIKE_POST"
    assert result.choices[0].finish_reason == "tool_calls"
    assert result.choices[0].message.tool_calls[0].function.name == "LIKE_POST"
    assert result.usage.total_tokens == 16
    assert "private-native-camel-reasoning" not in result.model_dump_json()
    follow_up = model.run([
        {"role": "user", "content": "Like post 3"},
        result.choices[0].message.model_dump(exclude_none=True),
        {"role": "tool", "tool_call_id": "call_like", "content": "done"},
    ])
    assert follow_up.choices[0].message.content == "Hello"
    assert captured[1]["input"] == [{"role": "user", "content": "Like post 3"}, *original_output, {"type": "function_call_output", "call_id": "call_like", "output": "done"}]


def test_llm_client_disables_automatic_subscription_retries():
    from app.utils.llm_client import LLMClient

    local = LLMClient(api_key=KEY, base_url="http://127.0.0.1:5001/api/chatgpt/v1", model="chatgpt-plan")
    other = LLMClient(api_key=KEY, base_url="https://api.openai.com/v1", model="gpt-test")
    assert local.client.max_retries == 0
    assert other.client.max_retries == 2
    local.client.close()
    other.client.close()


def test_json_format_is_translated(client, transport):
    captured = []
    transport(lambda request: captured.append(json.loads(request.content)) or completed_response())
    assert post(client, response_format={"type": "json_object"}).status_code == 200
    assert captured[0]["text"] == {"format": {"type": "json_object"}}


def test_completed_item_events_supply_text_when_terminal_output_is_empty(client, transport):
    item = {"id": "msg_live_shape", "type": "message", "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": "MiroFish connected"}]}
    events = [
        {"type": "response.output_text.delta", "output_index": 0, "delta": "MiroFish connected"},
        {"type": "response.output_item.done", "output_index": 0, "item": item},
        {"type": "response.completed", "response": completion([])},
    ]
    transport(lambda _: httpx.Response(200, text=sse(*events)))
    response = post(client)
    assert response.status_code == 200
    assert response.json["choices"][0]["message"]["content"] == "MiroFish connected"
    assert response.json["usage"]["total_tokens"] == 16


def test_completed_items_preserve_order_and_encrypted_tool_context(client, transport):
    original = _tool_output("call_stream_items", text="Checking.")
    captured = []
    def upstream(request):
        captured.append(json.loads(request.content))
        if len(captured) == 1:
            events = [{"type": "response.output_item.done", "output_index": i, "item": original[i]}
                      for i in reversed(range(len(original)))]
            return httpx.Response(200, text=sse(*events, {"type": "response.completed", "response": completion([])}))
        return completed_response()
    transport(upstream)
    first = post(client)
    assert first.status_code == 200
    message = first.json["choices"][0]["message"]
    assert message["content"] == "Checking."
    assert message["tool_calls"][0]["id"] == "call_stream_items"
    assert "opaque-encrypted-reasoning" not in first.text
    assert post(client, messages=_continue_with(message)).status_code == 200
    assert captured[1]["input"][1:-1] == original


@pytest.mark.parametrize('ending,code', [
    (None, 'interrupted_stream'),
    ({'type': 'response.incomplete', 'response': {'status': 'incomplete'}}, 'incomplete_response'),
    ({'type': 'response.failed', 'response': {'error': {'code': 'server_error'}}}, 'server_error'),
])
def test_done_output_items_never_override_missing_or_failed_terminal_event(client, transport, ending, code):
    events = [{'type': 'response.output_item.done', 'output_index': 0, 'item': completion()['output'][0]}]
    if ending:
        events.append(ending)
    transport(lambda _: httpx.Response(200, text=sse(*events)))
    response = post(client)
    assert response.status_code == 502
    assert response.json['error']['code'] == code
    assert 'Hello' not in response.text


def test_terminal_output_is_not_duplicated_by_completed_item_events(client, transport):
    item = completion()['output'][0]
    transport(lambda _: httpx.Response(200, text=sse(
        {'type': 'response.output_item.done', 'output_index': 0, 'item': item},
        {'type': 'response.completed', 'response': completion([item])})))
    assert post(client).json['choices'][0]['message']['content'] == 'Hello'


def test_empty_completed_response_fails_without_claiming_a_successful_reply(client, transport):
    transport(lambda _: completed_response([]))
    response = post(client)
    assert response.status_code == 502
    assert response.json['error']['code'] == 'empty_upstream_response'


@pytest.mark.parametrize("extra", [
    {"headers": {}},
    {"headers": {"Authorization": "Bearer incorrect"}},
    {"headers": {**HEADERS, "Origin": "https://untrusted.example"}},
    {"headers": {**HEADERS, "Origin": "null"}},
    {"headers": {**HEADERS, "Origin": "http://localhost:9999"}},
    {"headers": HEADERS, "environ_overrides": {"REMOTE_ADDR": "192.168.1.2"}},
    {"headers": HEADERS, "base_url": "http://malicious.example"},
])
def test_proxy_rejects_remote_and_unauthorized_access(client, transport, extra):
    calls = []
    transport(lambda request: calls.append(request) or completed_response())
    response = client.post(PATH, json={"messages": [{"role": "user", "content": "hello"}]}, **extra)
    assert response.status_code in (401, 403)
    assert not calls


@pytest.mark.parametrize("origin", ["http://localhost:3000", "http://127.0.0.1:3000"])
def test_frontend_origins_are_allowed(client, transport, origin):
    transport(lambda request: completed_response())
    response = client.post(PATH, json={"messages": [{"role": "user", "content": "Hello"}]}, headers={**HEADERS, "Origin": origin})
    assert response.status_code == 200


@pytest.mark.parametrize("event,expected_code", [
    ({"type": "response.incomplete", "response": {"status": "incomplete"}}, "incomplete_response"),
    ({"type": "response.output_text.delta", "delta": "Partial only"}, "interrupted_stream"),
    ({"type": "response.failed", "response": {"error": {"code": "server_error", "message": TOKEN}}}, "server_error"),
])
def test_incomplete_failed_and_interrupted_streams_do_not_succeed(client, transport, event, expected_code):
    transport(lambda request: httpx.Response(200, text=sse({"type": "response.output_text.delta", "delta": "partial text"}, event)))
    response = post(client, stream=True)
    assert response.status_code == 502
    assert response.json["error"]["code"] == expected_code
    assert TOKEN not in response.text
    assert "partial text" not in response.text


@pytest.mark.parametrize("status,code", [(403, "subscription_sharing_user_not_eligible"), (429, "subscription_sharing_usage_limit_exceeded")])
def test_upstream_limit_pauses_requests_preserves_status_and_redacts_message(app, client, transport, status, code):
    calls = []

    def upstream(request):
        calls.append(request)
        return httpx.Response(status, json={"error": {"code": code, "param": "model", "message": TOKEN}}, headers={"x-request-id": "req_safe"})

    transport(upstream)
    first = post(client)
    assert first.status_code == status
    assert first.json["error"]["code"] == code
    assert first.json["error"]["param"] == "model"
    assert first.headers["x-request-id"] == "req_safe"
    assert TOKEN not in first.text
    assert post(client).status_code == status
    assert len(calls) == 1
    with app.app_context():
        assert proxy.get_proxy_usage()["paused"] is True
        assert proxy.reset_proxy_usage()["requests_used"] == 0
    assert post(client).status_code == status
    assert len(calls) == 2


def test_usage_limit_that_arrives_after_stream_start_pauses_new_calls(app, client, transport):
    calls = []
    event = {"type": "response.failed", "response": {"error": {"code": "subscription_sharing_usage_limit_exceeded", "message": TOKEN}}}
    transport(lambda request: calls.append(request) or httpx.Response(200, text=sse(event)))
    response = post(client)
    assert response.status_code == 429
    assert response.json["error"]["code"] == "subscription_sharing_usage_limit_exceeded"
    assert post(client).status_code == 429
    assert len(calls) == 1


def test_local_budget_counts_failed_attempts_and_requires_explicit_reset(app, client, transport):
    calls = []
    transport(lambda request: calls.append(request) or httpx.Response(500, json={"detail": TOKEN}))
    assert [post(client).status_code for _ in range(3)] == [500, 500, 500]
    exhausted = post(client)
    assert exhausted.status_code == 429
    assert exhausted.json["error"]["code"] == "local_request_limit"
    assert len(calls) == 3
    with app.app_context():
        assert proxy.get_proxy_usage()["requests_used"] == 3
        proxy.reset_proxy_usage()
    assert post(client).status_code == 500
    assert len(calls) == 4


def test_concurrent_requests_are_bounded_and_active_budget_cannot_reset(app, transport):
    entered, release = threading.Event(), threading.Event()
    results = []

    def upstream(request):
        entered.set()
        assert release.wait(timeout=5)
        return completed_response()

    transport(upstream)

    def first_request():
        with app.test_client() as threaded_client:
            results.append(post(threaded_client).status_code)

    worker = threading.Thread(target=first_request)
    worker.start()
    try:
        assert entered.wait(timeout=3)
        with app.test_client() as client:
            rejected = post(client)
        assert rejected.status_code == 429
        assert rejected.json["error"]["code"] == "local_concurrency_limit"
        with app.app_context():
            with pytest.raises(proxy.SubscriptionProxyError) as error:
                proxy.reset_proxy_usage()
            assert error.value.status == 409
            assert proxy.get_proxy_usage()["requests_used"] == 1
    finally:
        release.set()
        worker.join(timeout=5)
    assert results == [200]
    with app.app_context():
        assert proxy.get_proxy_usage()["in_flight"] == 0


def test_transport_errors_release_concurrency_without_exposing_details(app, client, transport):
    def upstream(request):
        raise httpx.ReadTimeout(TOKEN)

    transport(upstream)
    response = post(client)
    assert response.status_code == 504
    assert TOKEN not in response.text
    with app.app_context():
        assert proxy.get_proxy_usage()["in_flight"] == 0
        assert proxy.get_proxy_usage()["paused"] is False


def test_chat_stream_contains_tool_calls_finish_reason_and_usage(client, transport):
    output = [{"type": "function_call", "name": "mirofish.LIKE_POST", "call_id": "call_like", "arguments": '{"post_id":3}'}]
    transport(lambda request: completed_response(output))
    response = post(client, stream=True, stream_options={"include_usage": True})
    assert response.status_code == 200
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ") and line != "data: [DONE]"]
    assert events[0]["choices"][0]["delta"]["tool_calls"][0] == {"index": 0, "id": "call_like", "type": "function", "function": {"name": "LIKE_POST", "arguments": '{"post_id":3}'}}
    assert events[1]["choices"][0]["finish_reason"] == "tool_calls"
    assert events[2]["choices"] == []
    assert events[2]["usage"]["total_tokens"] == 16
    assert response.text.endswith("data: [DONE]\n\n")


def test_model_catalog_is_account_specific_and_filtered(client, transport):
    def upstream(request):
        assert str(request.url) == proxy.MODELS_URL
        assert request.headers["Authorization"] == "Bearer " + TOKEN
        return httpx.Response(200, json={"models": [
            {"slug": "visible", "display_name": "Visible model", "visibility": "list"},
            {"slug": "hidden", "visibility": "hide"},
        ]})

    transport(upstream)
    response = client.get("/api/chatgpt/v1/models", headers=HEADERS)
    assert response.status_code == 200
    assert response.json["data"] == [{"id": "visible", "display_name": "Visible model", "object": "model", "created": 0, "owned_by": "openai"}]


@pytest.mark.parametrize("body", [
    {"messages": []},
    {"messages": [{"role": "tool", "content": "missing call id"}]},
    {"messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "https://example.com/image.png"}}]}]},
    {"n": 2},
    {"tools": [{"type": "web_search"}]},
    {"tools": [{"type": "function", "function": {"name": "invalid.name"}}]},
    {"functions": []},
])
def test_invalid_requests_are_rejected_before_upstream_access(client, transport, body):
    calls = []
    transport(lambda request: calls.append(request) or completed_response())
    response = post(client, **body)
    assert response.status_code == 400
    assert response.json["error"]["code"] == "invalid_request"
    assert not calls


def _tool_output(call_id, name="LIKE_POST", text=None, encrypted="opaque-encrypted-reasoning"):
    output = [{"id": "rs_" + call_id, "type": "reasoning", "summary": [], "encrypted_content": encrypted, "status": "completed"}]
    if text is not None:
        output.append({"id": "msg_" + call_id, "type": "message", "role": "assistant", "phase": "commentary", "content": [{"type": "output_text", "text": text}]})
    output.append({"id": "fc_" + call_id, "type": "function_call", "namespace": "mirofish", "name": name, "call_id": call_id, "arguments": '{"post_id":3}', "status": "completed"})
    return output


def _continue_with(message):
    return [{"role": "user", "content": "Like post 3"}, message, *[
        {"role": "tool", "tool_call_id": call["id"], "content": '{"ok":true}'}
        for call in message["tool_calls"]
    ]]


def test_reasoning_and_original_output_items_replay_once_across_multiple_tool_loops(client, transport):
    first_output = _tool_output("call_a", text="I will check the posts.", encrypted="private-reasoning-a")
    first_output += _tool_output("call_b", name="DISLIKE_POST", encrypted="private-reasoning-b")
    second_output = _tool_output("call_c", encrypted="private-reasoning-c")
    captured = []

    def upstream(request):
        captured.append(json.loads(request.content))
        if len(captured) == 1:
            return completed_response(first_output)
        if len(captured) == 2:
            return completed_response(second_output)
        return completed_response()

    transport(upstream)
    first = post(client, messages=[{"role": "user", "content": "Like post 3"}])
    assert first.status_code == 200
    assert "private-reasoning" not in first.text
    assert "encrypted_content" not in first.text
    first_message = first.json["choices"][0]["message"]
    second_history = _continue_with(first_message)
    second = post(client, messages=second_history)
    assert second.status_code == 200
    assert captured[1]["input"] == [
        {"role": "user", "content": "Like post 3"},
        *first_output,
        {"type": "function_call_output", "call_id": "call_a", "output": '{"ok":true}'},
        {"type": "function_call_output", "call_id": "call_b", "output": '{"ok":true}'},
    ]
    second_message = second.json["choices"][0]["message"]
    third_history = [*second_history, second_message, {"role": "tool", "tool_call_id": "call_c", "content": "done"}]
    third = post(client, messages=third_history)
    assert third.status_code == 200
    assert captured[2]["input"] == [*captured[1]["input"], *second_output, {"type": "function_call_output", "call_id": "call_c", "output": "done"}]
    assert captured[0]["include"] == ["reasoning.encrypted_content"]


@pytest.mark.parametrize("cache_change", ["clear", "expire", "account", "model"])
def test_missing_expired_or_other_connection_context_requires_new_simulation(client, transport, monkeypatch, cache_change):
    calls = []
    transport(lambda request: calls.append(request) or completed_response(_tool_output("call_a")))
    first = post(client)
    assert first.status_code == 200
    message = first.json["choices"][0]["message"]
    kwargs = {}
    if cache_change == "clear":
        proxy.clear_proxy_context()
    elif cache_change == "expire":
        monkeypatch.setattr(proxy._tool_context_cache, "ttl", 0)
    elif cache_change == "account":
        monkeypatch.setattr(proxy, "get_account_cache_scope", lambda: "other-account-login")
    else:
        kwargs["model"] = "other-model"
    response = post(client, messages=_continue_with(message), **kwargs)
    assert response.status_code == 409
    assert response.json["error"]["code"] == "tool_context_unavailable"
    assert "new simulation" in response.json["error"]["message"]
    assert len(calls) == 1


@pytest.mark.parametrize("history_change", ["arguments", "name", "text", "drop_parallel_call"])
def test_modified_tool_branch_is_rejected_before_inference(client, transport, history_change):
    calls = []
    output = [*_tool_output("call_a"), *_tool_output("call_b")]
    transport(lambda request: calls.append(request) or completed_response(output))
    first = post(client)
    message = first.json["choices"][0]["message"]
    if history_change == "arguments":
        message["tool_calls"][0]["function"]["arguments"] = '{"post_id":99}'
    elif history_change == "name":
        message["tool_calls"][0]["function"]["name"] = "CREATE_POST"
    elif history_change == "text":
        message["content"] = "modified assistant message"
    else:
        message["tool_calls"].pop()
    response = post(client, messages=_continue_with(message))
    assert response.status_code == 409
    assert response.json["error"]["code"] == "tool_context_mismatch"
    assert len(calls) == 1


def test_unknown_tool_context_is_not_guessed(client, transport):
    calls = []
    transport(lambda request: calls.append(request) or completed_response())
    message = {"role": "assistant", "content": None, "tool_calls": [{"id": "unknown", "type": "function", "function": {"name": "LIKE_POST", "arguments": "{}"}}]}
    response = post(client, messages=_continue_with(message))
    assert response.status_code == 409
    assert response.json["error"]["code"] == "tool_context_unavailable"
    assert not calls


def test_reasoning_without_encrypted_replay_context_is_not_accepted(client, transport):
    output = _tool_output("call_a")
    del output[0]["encrypted_content"]
    transport(lambda request: completed_response(output))
    response = post(client)
    assert response.status_code == 502
    assert response.json["error"]["code"] == "missing_reasoning_context"


def test_context_cache_evicts_groups_at_count_limit(client, transport, monkeypatch):
    monkeypatch.setattr(proxy, "_tool_context_cache", proxy._ToolContextCache(max_groups=1))
    calls = []
    transport(lambda request: calls.append(request) or completed_response(_tool_output("call_" + str(len(calls)))))
    first = post(client)
    assert first.status_code == 200
    assert post(client).status_code == 200
    assert len(proxy._tool_context_cache.groups) == 1
    response = post(client, messages=_continue_with(first.json["choices"][0]["message"]))
    assert response.status_code == 409
    assert len(calls) == 2


def test_context_cache_enforces_total_byte_limit_before_returning_tool_call(client, transport, monkeypatch):
    monkeypatch.setattr(proxy, "_tool_context_cache", proxy._ToolContextCache(max_bytes=128))
    transport(lambda request: completed_response(_tool_output("call_a", encrypted="large-reasoning" * 20)))
    response = post(client)
    assert response.status_code == 502
    assert response.json["error"]["code"] == "tool_context_too_large"
    assert proxy._tool_context_cache.size == 0


def test_context_cache_evicts_to_keep_combined_records_below_byte_limit(client, transport, monkeypatch):
    monkeypatch.setattr(proxy, "_tool_context_cache", proxy._ToolContextCache())
    calls = []
    transport(lambda request: calls.append(request) or completed_response(_tool_output("call_" + str(len(calls)), encrypted="opaque" * 100)))
    first = post(client)
    assert first.status_code == 200
    one_record_size = proxy._tool_context_cache.size
    monkeypatch.setattr(proxy._tool_context_cache, "max_bytes", one_record_size + 20)
    assert post(client).status_code == 200
    assert proxy._tool_context_cache.size <= proxy._tool_context_cache.max_bytes
    assert len(proxy._tool_context_cache.groups) == 1
    assert post(client, messages=_continue_with(first.json["choices"][0]["message"])).status_code == 409
    assert len(calls) == 2


def test_queued_request_rechecks_connection_before_dispatch(app, client, transport, monkeypatch):
    calls = []
    transport(lambda request: calls.append(request) or completed_response())
    with app.app_context():
        usage = proxy._usage()
    original_acquire = usage.acquire

    def admitted_after_sign_out():
        original_acquire()
        monkeypatch.setattr(proxy, "get_account_cache_scope", lambda: "new-login-session")

    monkeypatch.setattr(usage, "acquire", admitted_after_sign_out)
    response = post(client)
    assert response.status_code == 409
    assert response.json["error"]["code"] == "connection_changed"
    assert not calls
    with app.app_context():
        assert proxy.get_proxy_usage()["in_flight"] == 0
        assert proxy.get_proxy_usage()["paused"] is False
