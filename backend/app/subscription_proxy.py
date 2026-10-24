"""Local Chat Completions adapter for explicitly authorized ChatGPT plan use.

The public subscription route requires a stateless, streamed Responses request.
We buffer that stream until its terminal success event before returning anything
to MiroFish; an interrupted or failed generation must never look successful.
"""

from collections import OrderedDict
from copy import deepcopy
import hmac
import ipaddress
import json
import re
import threading
import time
from urllib.parse import urlsplit

import httpx
from flask import Blueprint, Response, current_app, jsonify, request

from .chatgpt_auth import SubscriptionAuthError, get_access_token, get_selected_model, get_account_cache_scope


chatgpt_proxy_bp = Blueprint("chatgpt_proxy", __name__)
RESPONSES_URL = "https://api.openai.com/v1/responses"
MODELS_URL = "https://api.openai.com/v1/models"
TOOL_NAMESPACE = "mirofish"
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.\[\]-]{1,160}$")
_FUNCTION_NAME = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_LOCAL_ORIGINS = {
    "http://localhost:3000", "http://127.0.0.1:3000",
    "http://localhost:5001", "http://127.0.0.1:5001",
}
_ERROR_STATUS = {
    "subscription_sharing_user_not_eligible": 403,
    "subscription_sharing_usage_limit_exceeded": 429,
    "subscription_sharing_usage_unavailable": 503,
    "subscription_sharing_unsupported_capability": 400,
    "subscription_sharing_route_not_supported": 403,
    "subscription_sharing_invalid_user": 401,
    "subscription_sharing_user_unavailable": 503,
    "chatpass_v2_scope_not_authorized": 403,
    "chatpass_v2_invalid_authorization_context": 403,
    "rate_limit_exceeded": 429,
}


class SubscriptionProxyError(Exception):
    """Safe public diagnostics; never initialized with upstream message text."""

    def __init__(self, code, message, status=400, *, param=None, request_id=None, upstream=False):
        super().__init__(message)
        self.code = code
        self.status = status
        self.param = param
        self.request_id = request_id
        self.upstream = upstream


class _Usage:
    def __init__(self, max_requests, max_concurrent, queue_timeout=60):
        self.lock = threading.Lock()
        self.available = threading.Condition(self.lock)
        self.max_requests = max(1, int(max_requests))
        self.max_concurrent = max(1, int(max_concurrent))
        self.queue_timeout = max(0, float(queue_timeout))
        self.requests_used = 0
        self.in_flight = 0
        self.paused_code = None
        self.paused_status = None

    def snapshot(self):
        with self.lock:
            return self._snapshot()

    def _snapshot(self):
        return {
            "requests_used": self.requests_used,
            "max_requests": self.max_requests,
            "in_flight": self.in_flight,
            "max_concurrent": self.max_concurrent,
            "paused": self.paused_code is not None,
            "paused_code": self.paused_code,
        }

    def acquire(self):
        deadline = time.monotonic() + self.queue_timeout
        with self.available:
            while True:
                if self.paused_code:
                    raise SubscriptionProxyError(
                        self.paused_code,
                        "ChatGPT requests are paused. Review the account connection and usage before resetting the local budget.",
                        self.paused_status,
                    )
                if self.requests_used >= self.max_requests:
                    raise SubscriptionProxyError(
                        "local_request_limit", "The local request budget is exhausted. Reset it explicitly to continue.", 429,
                    )
                if self.in_flight < self.max_concurrent:
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SubscriptionProxyError(
                        "local_concurrency_limit", "The local concurrent request queue timed out.", 429,
                    )
                self.available.wait(timeout=remaining)
            self.in_flight += 1
            # Failed upstream attempts also consume the local budget.
            self.requests_used += 1

    def release(self):
        with self.available:
            self.in_flight -= 1
            self.available.notify_all()

    def pause(self, error):
        if error.upstream and error.status in (401, 403, 429):
            with self.lock:
                self.paused_code = error.code
                self.paused_status = error.status

    def reset(self):
        with self.lock:
            if self.in_flight:
                raise SubscriptionProxyError("requests_in_flight", "Wait for active requests before resetting the budget.", 409)
            self.requests_used = 0
            self.paused_code = self.paused_status = None
            return self._snapshot()


_usage_init_lock = threading.Lock()


def _usage():
    with _usage_init_lock:
        if "chatgpt_proxy_usage" not in current_app.extensions:
            current_app.extensions["chatgpt_proxy_usage"] = _Usage(
                current_app.config.get("CHATGPT_MAX_REQUESTS", 100),
                current_app.config.get("CHATGPT_MAX_CONCURRENT", 2),
                current_app.config.get("CHATGPT_QUEUE_TIMEOUT", 60),
            )
        return current_app.extensions["chatgpt_proxy_usage"]


def get_proxy_usage():
    """Return this backend process's budget and pause state (requires app context)."""
    return _usage().snapshot()


def reset_proxy_usage():
    """Explicitly start a new local budget; raises on active requests."""
    return _usage().reset()


class _ToolContextCache:
    """Bounded RAM-only replay state, scoped to an authorized login and model.

    Chat Completions cannot carry Responses reasoning/phase fields through CAMEL.
    Keep complete original output groups privately instead. Never decode opaque
    encrypted reasoning or return it to the Chat client, status API, or logs.
    """

    def __init__(self, max_groups=256, max_bytes=16 * 1024 * 1024, ttl=24 * 3600):
        self.lock = threading.Lock()
        self.groups = OrderedDict()
        self.index = {}
        self.size = 0
        self.max_groups, self.max_bytes, self.ttl = max_groups, max_bytes, ttl

    def clear(self):
        with self.lock:
            self.groups.clear()
            self.index.clear()
            self.size = 0

    def _remove(self, key):
        group = self.groups.pop(key)
        self.size -= group["size"]
        for call in group["calls"]:
            self.index.pop((key[0], key[1], call["call_id"]), None)

    def _expire(self):
        now = time.monotonic()
        for key, group in list(self.groups.items()):
            if now - group["created"] >= self.ttl:
                self._remove(key)

    def remember(self, scope, model, result, chat_message):
        calls = [item for item in result["output"] if item.get("type") == "function_call"]
        if not calls:
            return
        for item in result["output"]:
            if item.get("type") == "reasoning" and (not isinstance(item.get("encrypted_content"), str) or not item["encrypted_content"]):
                raise SubscriptionProxyError(
                    "missing_reasoning_context", "ChatGPT omitted the encrypted context required to continue its tool call.", 502,
                )
        expected = [{
            "type": "function_call", "call_id": call["id"], "namespace": TOOL_NAMESPACE,
            "name": call["function"]["name"], "arguments": call["function"]["arguments"],
        } for call in chat_message["tool_calls"]]
        if len({call["call_id"] for call in expected}) != len(expected):
            raise SubscriptionProxyError("invalid_tool_call", "ChatGPT returned duplicate tool call identifiers.", 502)
        output = deepcopy(result["output"])
        group = {"output": output, "calls": expected, "text": chat_message.get("content") or "", "created": time.monotonic()}
        # Count the entire retained replay record, including its validation
        # metadata; reserve space for the bookkeeping field and index keys.
        size = len(json.dumps(group, ensure_ascii=False).encode("utf-8")) + 256 + sum(len(call["call_id"].encode("utf-8")) for call in expected)
        if size > self.max_bytes:
            raise SubscriptionProxyError("tool_context_too_large", "The tool continuation context exceeded the local memory limit.", 502)
        key = (scope, model, tuple(call["call_id"] for call in expected))
        group["size"] = size
        with self.lock:
            self._expire()
            # A repeated identifier with different context must not overwrite an
            # earlier agent branch. Real Responses call IDs are unique.
            if any((scope, model, call["call_id"]) in self.index for call in expected):
                raise SubscriptionProxyError("duplicate_tool_context", "ChatGPT returned a tool identifier that is already in use.", 502)
            while self.groups and (len(self.groups) >= self.max_groups or self.size + size > self.max_bytes):
                self._remove(next(iter(self.groups)))
            self.groups[key] = group
            self.size += size
            for call in expected:
                self.index[(scope, model, call["call_id"])] = key

    def replay(self, scope, model, calls, text):
        with self.lock:
            self._expire()
            key = self.index.get((scope, model, calls[0]["call_id"]))
            if key is None:
                raise SubscriptionProxyError(
                    "tool_context_unavailable", "The tool continuation context expired or belongs to another connection. Start a new simulation or agent conversation.", 409,
                )
            group = self.groups[key]
            if calls != group["calls"] or text != group["text"]:
                raise SubscriptionProxyError(
                    "tool_context_mismatch", "The tool history changed and cannot be replayed safely. Start a new simulation or agent conversation.", 409,
                )
            self.groups.move_to_end(key)
            # Keep the original reasoning, output message phases and function
            # item IDs in their original order, once per multi-call output group.
            return deepcopy(group["output"])


_tool_context_cache = _ToolContextCache()


def clear_proxy_context():
    """Drop private replay state on disconnect/account switch; needs no app context."""
    _tool_context_cache.clear()


def _is_loopback(value):
    try:
        return ipaddress.ip_address(value).is_loopback
    except (ValueError, TypeError):
        return False


@chatgpt_proxy_bp.before_request
def _guard_local_proxy():
    # Do not trust forwarding headers: this adapter is intentionally local only.
    if not _is_loopback(request.remote_addr):
        raise SubscriptionProxyError("local_only", "This model adapter only accepts local requests.", 403)
    host = urlsplit(request.host_url).hostname
    if host != "localhost" and not _is_loopback(host):
        raise SubscriptionProxyError("invalid_host", "Use the local MiroFish host.", 403)
    if request.headers.get("Origin") and request.headers["Origin"] not in _LOCAL_ORIGINS:
        raise SubscriptionProxyError("invalid_origin", "This origin is not allowed to use the local adapter.", 403)
    expected = current_app.config.get("LLM_API_KEY") or ""
    supplied = request.headers.get("Authorization", "")
    if not expected or not hmac.compare_digest(supplied.encode(), ("Bearer " + expected).encode()):
        raise SubscriptionProxyError("invalid_api_key", "A valid local model adapter key is required.", 401)


@chatgpt_proxy_bp.errorhandler(SubscriptionProxyError)
def _proxy_error(error):
    body = {"error": {"message": str(error), "type": "subscription_proxy_error", "code": error.code, "param": error.param}}
    response = jsonify(body)
    response.status_code = error.status
    if error.request_id:
        response.headers["x-request-id"] = error.request_id
    return response


def _invalid(message, param=None):
    raise SubscriptionProxyError("invalid_request", message, param=param)


def _function_name(name):
    if not isinstance(name, str) or not _FUNCTION_NAME.fullmatch(name):
        _invalid("Function names must contain only letters, digits, underscores, or hyphens.", "tools")
    return name


def _text_content(content):
    if isinstance(content, str):
        return content
    if content is None:
        return ""
    if isinstance(content, list):
        parts = []
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "text" or not isinstance(part.get("text"), str):
                _invalid("This MiroFish adapter currently accepts text message content only.", "messages")
            parts.append(part["text"])
        return "\n".join(parts)
    _invalid("Message content must be text or a text content array.", "messages")


def chat_to_responses(body, *, account_scope=None, model_override=None):
    """Translate full chat history, keeping tool calls/results linked by call_id."""
    if not isinstance(body, dict):
        _invalid("The request must be a JSON object.")
    model = model_override or body.get("model") or current_app.config.get("LLM_MODEL_NAME")
    if not isinstance(model, str) or not model.strip():
        _invalid("Choose a model from the connected ChatGPT account.", "model")
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        _invalid("Provide a nonempty messages array.", "messages")
    if body.get("n", 1) != 1:
        _invalid("Only one completion per request is supported.", "n")
    if not isinstance(body.get("stream", False), bool):
        _invalid("stream must be a boolean.", "stream")
    if body.get("functions") is not None or body.get("function_call") is not None:
        _invalid("Use tools and tool_choice instead of legacy functions.", "functions")

    items = []
    for message in messages:
        if not isinstance(message, dict):
            _invalid("Each message must be an object.", "messages")
        role = message.get("role")
        text = _text_content(message.get("content"))
        if role == "tool":
            call_id = message.get("tool_call_id")
            if not isinstance(call_id, str) or not call_id:
                _invalid("Tool results require a tool_call_id.", "messages")
            items.append({"type": "function_call_output", "call_id": call_id, "output": text})
            continue
        if role not in ("system", "developer", "user", "assistant"):
            _invalid("Unsupported message role.", "messages")
        calls = message.get("tool_calls") or []
        if calls and (role != "assistant" or not isinstance(calls, list)):
            _invalid("Only assistant messages may contain tool_calls.", "messages")
        message_items = []
        if text or not calls:
            # Explicit system input items are rejected on the subscription route.
            message_items.append({"role": "developer" if role == "system" else role, "content": text})
        call_items = []
        for call in calls:
            if not isinstance(call, dict) or call.get("type") != "function" or not isinstance(call.get("function"), dict):
                _invalid("Only function tool calls are supported.", "messages")
            function = call["function"]
            call_id, arguments = call.get("id"), function.get("arguments")
            if not isinstance(call_id, str) or not call_id or not isinstance(arguments, str):
                _invalid("Tool calls require an id and JSON arguments string.", "messages")
            call_items.append({
                "type": "function_call", "call_id": call_id,
                "namespace": TOOL_NAMESPACE, "name": _function_name(function.get("name")), "arguments": arguments,
            })

        if call_items and account_scope is not None:
            items.extend(_tool_context_cache.replay(account_scope, model.strip(), call_items, text))
        else:
            items.extend(message_items)
            items.extend(call_items)

    payload = {"model": model.strip(), "input": items, "store": False, "stream": True, "include": ["reasoning.encrypted_content"]}
    tools = body.get("tools") or []
    if not isinstance(tools, list):
        _invalid("tools must be an array.", "tools")
    functions = []
    names = set()
    for tool in tools:
        if not isinstance(tool, dict) or tool.get("type") != "function" or not isinstance(tool.get("function"), dict):
            _invalid("Only locally executed function tools are supported.", "tools")
        source = tool["function"]
        name = _function_name(source.get("name"))
        if name in names:
            _invalid("Function tool names must be unique.", "tools")
        names.add(name)
        function = {"type": "function", "name": name, "strict": source.get("strict", False)}
        for key in ("description", "parameters"):
            if key in source:
                function[key] = source[key]
        functions.append(function)
    if functions:
        payload["tools"] = [{"type": "namespace", "name": TOOL_NAMESPACE, "description": "MiroFish simulation actions, executed locally.", "tools": functions}]
    choice = body.get("tool_choice")
    if choice is not None:
        if isinstance(choice, str) and choice in ("auto", "none", "required"):
            payload["tool_choice"] = choice
        elif isinstance(choice, dict) and choice.get("type") == "function" and isinstance(choice.get("function"), dict):
            name = _function_name(choice["function"].get("name"))
            if name not in names:
                _invalid("The selected function is not in tools.", "tool_choice")
            payload["tool_choice"] = {"type": "function", "namespace": TOOL_NAMESPACE, "name": name}
        else:
            _invalid("Unsupported tool_choice.", "tool_choice")
    if "parallel_tool_calls" in body:
        if not isinstance(body["parallel_tool_calls"], bool):
            _invalid("parallel_tool_calls must be a boolean.", "parallel_tool_calls")
        payload["parallel_tool_calls"] = body["parallel_tool_calls"]
    response_format = body.get("response_format")
    if response_format is not None:
        if not isinstance(response_format, dict):
            _invalid("response_format must be an object.", "response_format")
        kind = response_format.get("type")
        if kind in ("text", "json_object"):
            payload["text"] = {"format": {"type": kind}}
        elif kind == "json_schema" and isinstance(response_format.get("json_schema"), dict):
            payload["text"] = {"format": {**response_format["json_schema"], "type": "json_schema"}}
        else:
            _invalid("Unsupported response_format.", "response_format")
    effort = body.get("reasoning_effort")
    if effort is not None:
        if effort not in ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"):
            _invalid("Unsupported reasoning_effort.", "reasoning_effort")
        payload["reasoning"] = {"effort": effort}
    # Sampling knobs and both Chat Completions token limit spellings are omitted:
    # the subscription preview explicitly rejects temperature/max_output_tokens.
    return payload


def _safe_identifier(value):
    return value if isinstance(value, str) and _SAFE_IDENTIFIER.fullmatch(value) else None


def _upstream_error(data, status=502, request_id=None):
    error = data.get("error") if isinstance(data, dict) else None
    error = error if isinstance(error, dict) else {}
    code = _safe_identifier(error.get("code")) or "upstream_error"
    message = {
        401: "ChatGPT did not accept this account authorization. Review the connection.",
        403: "ChatGPT plan use is restricted for this account, region, or integration.",
        429: "ChatGPT plan usage is limited. Review https://chatgpt.com/settings/usage before continuing.",
        503: "ChatGPT plan usage is temporarily unavailable. Try again later.",
    }.get(status, "ChatGPT could not complete the request. Check the error code and request ID.")
    return SubscriptionProxyError(code, message, status, param=_safe_identifier(error.get("param")), request_id=_safe_identifier(request_id), upstream=True)


def _http_client():
    # Never forward subscription credentials through environment-configured proxies
    # or follow redirects to an endpoint outside the documented public API.
    return httpx.Client(timeout=httpx.Timeout(180, connect=15), follow_redirects=False, trust_env=False)


def _sse_events(lines):
    data = []
    size = 0
    for line in lines:
        size += len(line)
        if size > 64 * 1024 * 1024:
            raise SubscriptionProxyError("upstream_response_too_large", "The model stream exceeded the local response limit.", 502)
        if line == "":
            if data:
                yield "\n".join(data)
                data = []
        elif line.startswith("data:"):
            data.append(line[5:].lstrip(" "))
    if data:
        yield "\n".join(data)


def _collect_completion(payload, token):
    # The subscription route can leave response.completed.output empty after
    # sending canonical output_item.done events. Keep those final items, while
    # still requiring the terminal success event before returning any content.
    completed_items = {}
    with _http_client() as client:
        with client.stream("POST", RESPONSES_URL, headers={"Authorization": "Bearer " + token, "Accept": "text/event-stream"}, json=payload) as upstream:
            request_id = upstream.headers.get("x-request-id")
            if upstream.status_code != 200:
                try:
                    data = json.loads(upstream.read())
                except (ValueError, UnicodeDecodeError):
                    data = {}
                raise _upstream_error(data, upstream.status_code, request_id)
            for event_data in _sse_events(upstream.iter_lines()):
                if event_data == "[DONE]":
                    break
                try:
                    event = json.loads(event_data)
                except ValueError:
                    raise SubscriptionProxyError("invalid_upstream_stream", "ChatGPT returned an invalid event stream.", 502) from None
                if not isinstance(event, dict):
                    raise SubscriptionProxyError("invalid_upstream_stream", "ChatGPT returned an invalid event stream.", 502)
                kind = event.get("type")
                result = event.get("response") or {}
                if kind == "response.output_item.done":
                    index, item = event.get("output_index"), event.get("item")
                    if type(index) is not int or not 0 <= index < 4096 or not isinstance(item, dict):
                        raise SubscriptionProxyError("invalid_upstream_stream", "ChatGPT returned an invalid completed output item.", 502)
                    if index in completed_items and completed_items[index] != item:
                        raise SubscriptionProxyError("invalid_upstream_stream", "ChatGPT returned conflicting completed output items.", 502)
                    completed_items[index] = item
                if kind in ("response.failed", "error"):
                    data = result if kind == "response.failed" else {"error": event.get("error", event)}
                    data = data if isinstance(data, dict) else {}
                    error = data.get("error") or {}
                    error = error if isinstance(error, dict) else {}
                    status = _ERROR_STATUS.get(error.get("code"), 502)
                    raise _upstream_error(data, status, request_id)
                if kind == "response.incomplete":
                    raise SubscriptionProxyError("incomplete_response", "ChatGPT stopped before completing the response.", 502, request_id=_safe_identifier(request_id))
                if kind == "response.completed":
                    if not isinstance(result, dict) or result.get("status", "completed") != "completed":
                        raise SubscriptionProxyError("invalid_upstream_stream", "ChatGPT returned an invalid completion event.", 502)
                    terminal_output = result.get("output", [])
                    if not isinstance(terminal_output, list):
                        raise SubscriptionProxyError("invalid_upstream_stream", "ChatGPT returned an invalid completed output list.", 502)
                    output = []
                    last_index = max(len(terminal_output) - 1, max(completed_items, default=-1))
                    for index in range(last_index + 1):
                        terminal_item = terminal_output[index] if index < len(terminal_output) else None
                        item = completed_items.get(index, terminal_item)
                        if not isinstance(item, dict):
                            raise SubscriptionProxyError("invalid_upstream_stream", "ChatGPT omitted a completed output item.", 502)
                        if isinstance(terminal_item, dict) and terminal_item.get("id") and item.get("id") and terminal_item["id"] != item["id"]:
                            raise SubscriptionProxyError("invalid_upstream_stream", "ChatGPT returned inconsistent output item identifiers.", 502)
                        output.append(item)
                    if not output:
                        raise SubscriptionProxyError("empty_upstream_response", "ChatGPT completed without returning a reply or tool call.", 502)
                    return {**result, "output": output}, _safe_identifier(request_id)
    raise SubscriptionProxyError("interrupted_stream", "The ChatGPT stream ended before completion. No partial response was accepted.", 502)


def responses_to_chat(result, model):
    if not isinstance(result.get("output"), list):
        raise SubscriptionProxyError("invalid_upstream_response", "ChatGPT returned an invalid completed response.", 502)
    message = {"role": "assistant", "content": None}
    text, calls, refusals = [], [], []
    for item in result.get("output", []):
        if not isinstance(item, dict):
            raise SubscriptionProxyError("invalid_upstream_response", "ChatGPT returned an invalid output item.", 502)
        if item.get("type") == "message":
            for part in item.get("content", []):
                if not isinstance(part, dict):
                    raise SubscriptionProxyError("invalid_upstream_response", "ChatGPT returned invalid message content.", 502)
                if part.get("type") == "output_text":
                    text.append(part.get("text", ""))
                elif part.get("type") == "refusal":
                    refusals.append(part.get("refusal", ""))
        elif item.get("type") == "function_call":
            if item.get("namespace") not in (None, TOOL_NAMESPACE):
                raise SubscriptionProxyError("unexpected_tool_namespace", "ChatGPT returned a tool from an unexpected namespace.", 502)
            name = item.get("name", "")
            if name.startswith(TOOL_NAMESPACE + "."):
                name = name[len(TOOL_NAMESPACE) + 1:]
            if not isinstance(name, str) or not _FUNCTION_NAME.fullmatch(name) or not isinstance(item.get("call_id"), str) or not isinstance(item.get("arguments"), str):
                raise SubscriptionProxyError("invalid_tool_call", "ChatGPT returned an invalid tool call.", 502)
            calls.append({"id": item["call_id"], "type": "function", "function": {"name": name, "arguments": item["arguments"]}})
    if text:
        message["content"] = "".join(text)
    if calls:
        message["tool_calls"] = calls
    if refusals:
        message["refusal"] = "".join(refusals)
    if not calls and message["content"] is None:
        message["content"] = ""
    usage = result.get("usage") or {}
    completion = {
        "id": result.get("id", "chatcmpl-local"), "object": "chat.completion",
        "created": result.get("created_at", int(time.time())), "model": result.get("model", model),
        "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if calls else "stop", "logprobs": None}],
        "usage": {"prompt_tokens": usage.get("input_tokens", 0), "completion_tokens": usage.get("output_tokens", 0), "total_tokens": usage.get("total_tokens", 0)},
    }
    if "output_tokens_details" in usage:
        completion["usage"]["completion_tokens_details"] = usage["output_tokens_details"]
    if "input_tokens_details" in usage:
        completion["usage"]["prompt_tokens_details"] = usage["input_tokens_details"]
    return completion


def _chat_stream(completion, include_usage=False):
    base = {key: completion[key] for key in ("id", "created", "model")}
    base["object"] = "chat.completion.chunk"
    choice = completion["choices"][0]
    delta = dict(choice["message"])
    if "tool_calls" in delta:
        delta["tool_calls"] = [{"index": index, **call} for index, call in enumerate(delta["tool_calls"])]
    yield "data: " + json.dumps({**base, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]}, ensure_ascii=False) + "\n\n"
    yield "data: " + json.dumps({**base, "choices": [{"index": 0, "delta": {}, "finish_reason": choice["finish_reason"]}]}) + "\n\n"
    if include_usage:
        yield "data: " + json.dumps({**base, "choices": [], "usage": completion["usage"]}) + "\n\n"
    yield "data: [DONE]\n\n"


@chatgpt_proxy_bp.route("/api/chatgpt/v1/chat/completions", methods=["POST"])
def chat_completions():
    body = request.get_json(silent=True)
    payload = chat_to_responses(body)
    try:
        payload["model"] = get_selected_model(payload["model"])
        account_scope = get_account_cache_scope()
    except SubscriptionAuthError as error:
        raise SubscriptionProxyError(error.code, str(error), error.status_code) from None
    payload = chat_to_responses(body, account_scope=account_scope, model_override=payload["model"])
    usage = _usage()
    usage.acquire()
    try:
        try:
            # Admission can queue. Never retain a credential from before a sign
            # out/account switch, or dispatch a queued request to a new account.
            if get_account_cache_scope() != account_scope:
                raise SubscriptionProxyError("connection_changed", "The ChatGPT connection changed while this request was queued. Start the operation again.", 409)
            token = get_access_token()
            requested_model = body.get("model") or current_app.config.get("LLM_MODEL_NAME")
            if get_account_cache_scope() != account_scope or get_selected_model(requested_model) != payload["model"]:
                raise SubscriptionProxyError("connection_changed", "The ChatGPT connection or selected model changed while this request was queued. Start the operation again.", 409)
        except SubscriptionAuthError as error:
            raise SubscriptionProxyError(error.code, str(error), error.status_code) from None
        result, request_id = _collect_completion(payload, token)
        completion = responses_to_chat(result, payload["model"])
        _tool_context_cache.remember(account_scope, payload["model"], result, completion["choices"][0]["message"])
    except SubscriptionProxyError as error:
        usage.pause(error)
        raise
    except httpx.TimeoutException:
        raise SubscriptionProxyError("upstream_timeout", "The ChatGPT request timed out before completion.", 504) from None
    except httpx.HTTPError:
        raise SubscriptionProxyError("upstream_connection_error", "The ChatGPT connection failed before completion.", 502) from None
    finally:
        usage.release()
    if body.get("stream"):
        options = body.get("stream_options") or {}
        response = Response(_chat_stream(completion, isinstance(options, dict) and options.get("include_usage", False)), mimetype="text/event-stream")
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Accel-Buffering"] = "no"
    else:
        response = jsonify(completion)
    if request_id:
        response.headers["x-request-id"] = request_id
    return response


@chatgpt_proxy_bp.route("/api/chatgpt/v1/models", methods=["GET"])
def models():
    try:
        token = get_access_token()
        with _http_client() as client:
            upstream = client.get(MODELS_URL, headers={"Authorization": "Bearer " + token})
        if upstream.status_code != 200:
            try:
                data = upstream.json()
            except ValueError:
                data = {}
            raise _upstream_error(data, upstream.status_code, upstream.headers.get("x-request-id"))
        catalog = upstream.json()
        if not isinstance(catalog, dict) or not isinstance(catalog.get("models"), list):
            raise SubscriptionProxyError("invalid_model_catalog", "ChatGPT returned an invalid account model catalog.", 502)
        data = catalog.get("models", [])
        return jsonify({"object": "list", "data": [
            {"id": model["slug"], "object": "model", "created": 0, "owned_by": "openai", "display_name": model.get("display_name", model["slug"])}
            for model in data if isinstance(model, dict) and model.get("visibility") == "list" and isinstance(model.get("slug"), str)
        ]})
    except SubscriptionAuthError as error:
        raise SubscriptionProxyError(error.code, str(error), error.status_code) from None
    except httpx.TimeoutException:
        raise SubscriptionProxyError("upstream_timeout", "The ChatGPT model list request timed out.", 504) from None
    except (httpx.HTTPError, ValueError):
        raise SubscriptionProxyError("upstream_connection_error", "The ChatGPT model list could not be loaded.", 502) from None
