import asyncio
import json

import httpx
import pytest

from casedesk import providers
from casedesk.providers import Provider, ProviderError


def response_payload(arguments='{"query":"refund"}'):
    return {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {"name": "search_knowledge", "arguments": arguments},
                        }
                    ]
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 12, "completion_tokens": 8},
    }


def provider(handler, **kwargs):
    return Provider(
        "https://provider.example/v1/",
        "test-secret",
        "configured-model",
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


async def test_tool_request_shape_and_usage():
    def handler(request):
        assert str(request.url) == "https://provider.example/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer test-secret"
        body = json.loads(request.content)
        assert body["model"] == "configured-model" and body["tool_choice"] == "required"
        return httpx.Response(200, json=response_payload())

    result = await provider(handler).complete([{"role": "user", "content": "help"}], [])
    assert result.calls[0].arguments == {"query": "refund"}
    assert (result.input_tokens, result.output_tokens) == (12, 8)


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_transient_status_retries_bounded(monkeypatch, status):
    attempts, delays = [], []

    async def sleep(delay):
        delays.append(delay)

    def handler(_request):
        attempts.append(1)
        return httpx.Response(status, text="private upstream secret")

    monkeypatch.setattr(providers.asyncio, "sleep", sleep)
    with pytest.raises(ProviderError) as error:
        await provider(handler).complete([], [])
    assert len(attempts) == 3 and delays == [0.25, 0.5]
    assert "private" not in str(error.value)


@pytest.mark.parametrize("status", [301, 400, 401, 403, 422])
async def test_nontransient_errors_never_retry_or_forward_secret(status):
    attempts = []

    def handler(_request):
        attempts.append(1)
        return httpx.Response(
            status, text="test-secret", headers={"Location": "https://evil.example"}
        )

    with pytest.raises(ProviderError) as error:
        await provider(handler).complete([], [])
    assert len(attempts) == 1
    assert "test-secret" not in str(error.value)


@pytest.mark.parametrize(
    "arguments", ["[]", "{invalid", '{"value":NaN}', "null", {"query": "refund"}]
)
async def test_malformed_tool_arguments_fail(arguments):
    with pytest.raises(ProviderError):
        await provider(lambda _: httpx.Response(200, json=response_payload(arguments))).complete(
            [], []
        )


@pytest.mark.parametrize(
    "body", [b"[]", b"private error", b'{"choices":[]}', b'{"choices":[null]}']
)
async def test_malformed_completion_does_not_expose_body(body):
    with pytest.raises(ProviderError) as error:
        await provider(lambda _: httpx.Response(200, content=body)).complete([], [])
    assert "private" not in str(error.value)


async def test_stream_size_limit_and_timeout(monkeypatch):
    monkeypatch.setattr(providers, "MAX_RESPONSE", 50)

    class HugeStream(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            yield b"x" * 60

        async def aclose(self):
            self.closed = True

    stream = HugeStream()
    with pytest.raises(ProviderError):
        await provider(lambda _: httpx.Response(200, stream=stream)).complete([], [])
    assert stream.closed

    class SlowStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            await asyncio.sleep(0.02)
            yield b"data"

    with pytest.raises(ProviderError):
        await provider(
            lambda _: httpx.Response(200, stream=SlowStream()), timeout=0.001, retries=0
        ).complete([], [])


async def test_cancelled_network_operation_propagates():
    def handler(_request):
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await provider(handler).complete([], [])


async def test_request_limit_prevents_io(monkeypatch):
    monkeypatch.setattr(providers, "MAX_REQUEST", 20)

    def handler(_):
        pytest.fail("Request limit must be checked before I/O")

    with pytest.raises(ProviderError):
        await provider(handler).complete([], [])


@pytest.mark.parametrize(
    "base",
    [
        "file:///secret",
        "https://user:secret@provider.test",
        "https://provider.test?key=secret",
        "http://[",
        123,
    ],
)
def test_invalid_endpoint_is_rejected(base):
    with pytest.raises(ProviderError) as error:
        Provider(base, "test-secret", "model")
    assert "secret" not in str(error.value)
