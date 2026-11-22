"""LLM providers. The office PCs never run a model: the server calls one of these.

- anthropic: cloud model through the official SDK (default when ANTHROPIC_API_KEY is set)
- ollama:    a model hosted on your own machine (for example the gaming laptop)
- none:      no model; the app still returns the matching passages with citations
"""
import json

import httpx

from . import config

_client = None


def available():
    return config.LLM_PROVIDER in ("anthropic", "ollama")


def describe():
    if config.LLM_PROVIDER == "anthropic":
        return "anthropic / " + config.ANTHROPIC_MODEL
    if config.LLM_PROVIDER == "ollama":
        return "ollama / " + config.OLLAMA_MODEL
    return "none (search-only)"


async def stream(system, messages, max_tokens=8000):
    """Yield text pieces. Raises LLMError with a message that is safe to show the user."""
    if config.LLM_PROVIDER == "anthropic":
        async for t in _anthropic(system, messages, max_tokens):
            yield t
    elif config.LLM_PROVIDER == "ollama":
        async for t in _ollama(system, messages):
            yield t
    else:
        raise LLMError("No language model is configured on the server.")


async def complete(system, messages, max_tokens=400):
    return "".join([t async for t in stream(system, messages, max_tokens)]).strip()


class LLMError(Exception):
    pass


async def _anthropic(system, messages, max_tokens):
    import anthropic

    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic()
    kwargs = dict(model=config.ANTHROPIC_MODEL, max_tokens=max_tokens, system=system, messages=messages,
                  output_config={"effort": config.LLM_EFFORT})
    if "haiku" in config.ANTHROPIC_MODEL:
        # Short, grounded rewording: skip thinking so the first words appear sooner.
        kwargs["thinking"] = {"type": "disabled"}
    try:
        async with _client.messages.stream(**kwargs) as s:
            async for text in s.text_stream:
                yield text
            final = await s.get_final_message()
        if final.stop_reason == "refusal":
            raise LLMError("The model declined to answer this question.")
    except anthropic.AuthenticationError:
        raise LLMError("The server's Anthropic API key is missing or invalid.")
    except anthropic.RateLimitError:
        raise LLMError("The model is busy (rate limit). Please try again in a moment.")
    except anthropic.APIStatusError as e:
        raise LLMError("Model error %s: %s" % (e.status_code, e.message))
    except anthropic.APIConnectionError:
        raise LLMError("The server could not reach the cloud model. Check its internet connection.")


async def _ollama(system, messages):
    # Without num_ctx Ollama silently cuts long prompts, which would drop sources.
    body = {"model": config.OLLAMA_MODEL, "stream": True, "options": {"num_ctx": 8192, "temperature": 0},
            "messages": [{"role": "system", "content": system}] + messages}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=5)) as c:
            async with c.stream("POST", config.OLLAMA_URL + "/api/chat", json=body) as r:
                if r.status_code != 200:
                    raise LLMError("Ollama returned HTTP %s. Is the model '%s' pulled?" % (r.status_code, config.OLLAMA_MODEL))
                async for line in r.aiter_lines():
                    if line.strip():
                        piece = json.loads(line).get("message", {}).get("content", "")
                        if piece:
                            yield piece
    except httpx.HTTPError:
        raise LLMError("The server could not reach Ollama at %s." % config.OLLAMA_URL)
