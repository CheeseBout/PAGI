"""Importing this package registers every builtin tool into TOOL_REGISTRY."""

from __future__ import annotations

from . import (  # noqa: F401
    delegate_task,
    delete_file,
    edit_file,
    execute_code,
    fetch_url,
    get_current_datetime,
    http_request,
    list_files,
    rag_search,
    read_file,
    search_files,
    web_search,
    write_file,
)
