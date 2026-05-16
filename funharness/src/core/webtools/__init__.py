"""Web tools registered into the core tool registry."""

from .crawl import tool_web_crawl
from .fetch import tool_web_fetch
from .search import tool_web_search

__all__ = ["tool_web_crawl", "tool_web_fetch", "tool_web_search"]
