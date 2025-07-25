# Copyright 2024 Marimo. All rights reserved.
from __future__ import annotations

import asyncio
import contextlib
import socket
from typing import TYPE_CHECKING

from marimo import _loggers
from marimo._server.api.deps import AppState, AppStateBase
from marimo._server.api.interrupt import InterruptHandler
from marimo._server.api.utils import open_url_in_browser
from marimo._server.file_router import AppFileRouter
from marimo._server.lsp import any_lsp_server_running
from marimo._server.model import SessionMode
from marimo._server.print import (
    print_experimental_features,
    print_shutdown,
    print_startup,
)
from marimo._server.sessions import SessionManager
from marimo._server.tokens import AuthToken
from marimo._server.utils import initialize_mimetypes
from marimo._server.uvicorn_utils import close_uvicorn

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from starlette.applications import Starlette

LOGGER = _loggers.marimo_logger()


@contextlib.asynccontextmanager
async def lsp(app: Starlette) -> AsyncIterator[None]:
    state = AppState.from_app(app)
    user_config = state.config_manager.get_config()
    session_mgr = state.session_manager

    # Only start the LSP server in Edit mode
    if session_mgr.mode == SessionMode.EDIT:
        if any_lsp_server_running(user_config):
            LOGGER.debug("Language Servers are enabled")
            await session_mgr.start_lsp_server()

    yield


@contextlib.asynccontextmanager
async def mcp(app: Starlette) -> AsyncIterator[None]:
    state = AppState.from_app(app)
    session_mgr = state.session_manager

    # Only start MCP servers in Edit mode
    if session_mgr.mode == SessionMode.EDIT:
        # add MCP server here after it is implemented
        ...

    yield


@contextlib.asynccontextmanager
async def open_browser(app: Starlette) -> AsyncIterator[None]:
    state = AppState.from_app(app)
    if not state.headless:
        url = _startup_url(state)
        user_config = state.config_manager.get_config()
        browser = user_config["server"]["browser"]
        # Wait 20ms for the server to start and then open the browser, but this
        # function must complete
        asyncio.get_running_loop().call_later(
            0.02, open_url_in_browser, browser, url
        )
    yield


@contextlib.asynccontextmanager
async def logging(app: Starlette) -> AsyncIterator[None]:
    state = AppState.from_app(app)
    manager: SessionManager = state.session_manager
    file_router = manager.file_router

    # Startup message
    if not manager.quiet:
        file = file_router.maybe_get_single_file()
        print_startup(
            file_name=file.name if file else None,
            url=_startup_url(state),
            run=manager.mode == SessionMode.RUN,
            new=file_router.get_unique_file_key() == AppFileRouter.NEW_FILE,
            network=state.host == "0.0.0.0",
        )

        print_experimental_features(state.config_manager.get_config())

    yield

    # Shutdown message
    if not manager.quiet:
        print_shutdown()


@contextlib.asynccontextmanager
async def signal_handler(app: Starlette) -> AsyncIterator[None]:
    state = AppState.from_app(app)
    manager = state.session_manager

    # Interrupt handler
    def shutdown() -> None:
        manager.shutdown()
        if state.server:
            close_uvicorn(state.server)

    InterruptHandler(
        quiet=manager.quiet,
        shutdown=shutdown,
    ).register()
    yield


@contextlib.asynccontextmanager
async def etc(app: Starlette) -> AsyncIterator[None]:
    del app
    # Mimetypes
    initialize_mimetypes()
    yield


def _startup_url(state: AppStateBase) -> str:
    host = state.host
    port = state.port
    try:
        # pretty printing:
        # if the address maps to localhost, print "localhost" to stdout
        if (
            socket.getnameinfo((host, port), socket.NI_NOFQDN)[0]
            == "localhost"
        ):
            host = "localhost"
    except Exception:
        # aggressive try/except in case of platform-specific quirks;
        # nothing to handle, since the `try` logic is just for pretty
        # printing the host name
        ...

    url = f"http://{host}:{port}{state.base_url}"
    if port == 80:
        url = f"http://{host}{state.base_url}"
    elif port == 443:
        url = f"https://{host}{state.base_url}"

    if AuthToken.is_empty(state.session_manager.auth_token):
        return url
    return f"{url}?access_token={str(state.session_manager.auth_token)}"
