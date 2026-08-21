"""Tests for the composite ``web_search`` tool.

``web_search`` used to drive the Playwright MCP server (browser_navigate +
browser_snapshot) and parse an accessibility snapshot. It now issues one plain HTTPS GET
to DuckDuckGo's HTML endpoint and parses the real markup — the endpoint never needed
JavaScript, so the headless browser was pure transport overhead. These tests pin the
contract the tool layer depends on, which is unchanged by that re-transport.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from tests.conftest import TEST_USER_ID, TEST_WORKSPACE_ID

_MODULE = "src.services.web_search"

# A trimmed but structurally faithful DuckDuckGo HTML response: results are anchors with
# class ``result__a`` whose href is a /l/?uddg= redirect wrapper, followed by a
# ``result__snippet`` anchor whose text may contain nested markup.
_DDG_HTML = """
<html><body>
<div class="result results_links results_links_deep web-result">
  <h2 class="result__title">
    <a rel="nofollow" class="result__a"
       href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fpython.org%2Ftutorial&amp;rut=ab12"
       >Python Tutorial</a>
  </h2>
  <a class="result__snippet" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fpython.org%2Ftutorial">
     Learn Python <b>programming</b> step by step
  </a>
</div>
<div class="result results_links results_links_deep web-result">
  <h2 class="result__title">
    <a rel="nofollow" class="result__a"
       href="//duckduckgo.com/l/?uddg=https%3A%2F%2Frealpython.com&amp;rut=cd34">Real Python</a>
  </h2>
  <a class="result__snippet" href="#">Python tutorials, guides and articles</a>
</div>
</body></html>
"""


def _mock_client(*, html: str = _DDG_HTML, status: int = 200, raises: Exception | None = None):
    """Return a patchable ``httpx.AsyncClient`` whose GET yields ``html``."""
    response = MagicMock()
    response.status_code = status
    response.text = html
    response.raise_for_status = MagicMock()

    client = MagicMock()
    client.get = AsyncMock(side_effect=raises) if raises else AsyncMock(return_value=response)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    factory = MagicMock(return_value=ctx)
    return factory, client


async def test_returns_structured_results_with_unwrapped_urls():
    """The DDG /l/?uddg= redirect wrapper is unwrapped to the real destination URL.

    Returning the wrapper would hand every downstream consumer — memories, entity
    extraction, the Perceiver's findings — a duckduckgo.com URL instead of the source.
    """
    factory, client = _mock_client()
    with patch(f"{_MODULE}.httpx.AsyncClient", factory):
        from src.services.web_search import web_search

        result = await web_search(
            query="python tutorial", user_id=TEST_USER_ID, workspace_id=TEST_WORKSPACE_ID
        )

    assert result["status"] == "ok"
    assert result["provider"] == "duckduckgo"
    assert result["query"] == "python tutorial"
    assert result["total"] == 2
    assert result["results"][0]["title"] == "Python Tutorial"
    assert result["results"][0]["url"] == "https://python.org/tutorial"
    assert "programming" in result["results"][0]["snippet"]
    assert result["results"][1]["url"] == "https://realpython.com"


async def test_snippet_strips_nested_markup():
    """Snippet text is the concatenated text of the anchor, not raw HTML."""
    factory, _ = _mock_client()
    with patch(f"{_MODULE}.httpx.AsyncClient", factory):
        from src.services.web_search import web_search

        result = await web_search(query="python", user_id=TEST_USER_ID)

    snippet = result["results"][0]["snippet"]
    assert "<b>" not in snippet
    assert snippet == "Learn Python programming step by step"


async def test_empty_query_returns_error_without_a_request():
    factory, client = _mock_client()
    with patch(f"{_MODULE}.httpx.AsyncClient", factory):
        from src.services.web_search import web_search

        result = await web_search(query="   ", user_id=TEST_USER_ID)

    assert result["status"] == "error"
    assert "query is required" in result["error"]
    assert result["results"] == []
    client.get.assert_not_awaited()


async def test_no_results_is_ok_not_error():
    """An honest empty result set is a successful search, not a failure."""
    factory, _ = _mock_client(html="<html><body><p>No results.</p></body></html>")
    with patch(f"{_MODULE}.httpx.AsyncClient", factory):
        from src.services.web_search import web_search

        result = await web_search(query="asdkjhaskdjh", user_id=TEST_USER_ID)

    assert result["status"] == "ok"
    assert result["results"] == []
    assert result["total"] == 0


async def test_network_failure_is_reported_not_raised():
    """The tool layer expects a dict; an exception here would surface as a tool crash."""
    factory, _ = _mock_client(raises=RuntimeError("connection reset"))
    with patch(f"{_MODULE}.httpx.AsyncClient", factory):
        from src.services.web_search import web_search

        result = await web_search(query="python", user_id=TEST_USER_ID)

    assert result["status"] == "error"
    assert "connection reset" in result["error"]
    assert result["results"] == []


async def test_num_results_is_capped_and_floored():
    many = "".join(
        f'<div class="result"><h2><a class="result__a" '
        f'href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2F{i}">R{i}</a></h2>'
        f'<a class="result__snippet">S{i}</a></div>'
        for i in range(30)
    )
    factory, _ = _mock_client(html=f"<html><body>{many}</body></html>")
    with patch(f"{_MODULE}.httpx.AsyncClient", factory):
        from src.services.web_search import web_search

        capped = await web_search(query="x", num_results=999, user_id=TEST_USER_ID)
        floored = await web_search(query="x", num_results=0, user_id=TEST_USER_ID)

    assert capped["total"] == 20
    assert floored["total"] == 1


async def test_no_mcp_browser_call_remains():
    """Regression fence for the browser-stack removal.

    ``web_search`` was the last consumer of the Playwright MCP server. If a future edit
    reintroduces an MCP browser call here, the server is gone, so it would fail at runtime
    rather than at import — pin it at the source level instead.

    This checks for *calls*, not for the word "playwright": the module docstring explains
    why the browser transport was dropped, and that history is worth keeping. Comments are
    stripped before matching so only executable lines are fenced.
    """
    import inspect
    import io
    import tokenize

    import src.services.web_search as mod

    source = inspect.getsource(mod)
    code = "".join(
        tok.string if tok.type not in (tokenize.COMMENT, tokenize.STRING) else '""'
        for tok in tokenize.generate_tokens(io.StringIO(source).readline)
    )
    for forbidden in ("call_mcp_tool", "browser_navigate", "browser_snapshot", "mcp_bridge"):
        assert forbidden not in code, f"{forbidden} reintroduced into web_search"
