# Copyright 2024 Marimo. All rights reserved.
from __future__ import annotations

from typing import TYPE_CHECKING

from starlette.authentication import requires
from starlette.exceptions import HTTPException
from starlette.responses import HTMLResponse, PlainTextResponse

from marimo import _loggers
from marimo._server.api.deps import AppState
from marimo._server.api.status import HTTPStatus
from marimo._server.api.utils import parse_request
from marimo._server.export.exporter import AutoExporter, Exporter
from marimo._server.model import SessionMode
from marimo._server.models.export import (
    ExportAsHTMLRequest,
    ExportAsMarkdownRequest,
    ExportAsScriptRequest,
)
from marimo._server.models.models import SuccessResponse
from marimo._server.router import APIRouter

if TYPE_CHECKING:
    from starlette.requests import Request

LOGGER = _loggers.marimo_logger()

# Router for export endpoints
router = APIRouter()

auto_exporter = AutoExporter()


@router.post("/html")
@requires("read")
async def export_as_html(
    *,
    request: Request,
) -> HTMLResponse:
    """
    requestBody:
        content:
            application/json:
                schema:
                    $ref: "#/components/schemas/ExportAsHTMLRequest"
    responses:
        200:
            description: Export the notebook as HTML
            content:
                text/html:
                    schema:
                        type: string
        400:
            description: File must be saved before downloading
    """
    app_state = AppState(request)
    body = await parse_request(request, cls=ExportAsHTMLRequest)
    session = app_state.require_current_session()

    # Check if the file is named
    if not session.app_file_manager.is_notebook_named:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail="File must have a name before exporting",
        )

    # Only include the code and console if we are in edit mode
    if app_state.mode != SessionMode.EDIT:
        body.include_code = False

    html, filename = Exporter().export_as_html(
        app=session.app_file_manager.app,
        filename=session.app_file_manager.filename,
        session_view=session.session_view,
        display_config=session.config_manager.get_config()["display"],
        request=body,
    )

    if body.download:
        headers = {"Content-Disposition": f"attachment; filename={filename}"}
    else:
        headers = {}

    # Download the HTML
    return HTMLResponse(
        content=html,
        headers=headers,
    )


@router.post("/auto_export/html")
@requires("edit")
async def auto_export_as_html(
    *,
    request: Request,
) -> SuccessResponse | PlainTextResponse:
    """
    requestBody:
        content:
            application/json:
                schema:
                    $ref: "#/components/schemas/ExportAsHTMLRequest"
    responses:
        200:
            description: Export the notebook as HTML
            content:
                application/json:
                    schema:
                        $ref: "#/components/schemas/SuccessResponse"
        400:
            description: File must be saved before downloading
    """
    app_state = AppState(request)
    body = await parse_request(request, cls=ExportAsHTMLRequest)
    session = app_state.require_current_session()

    # Reload the file manager to get the latest state
    session.app_file_manager.reload()
    session_view = session.session_view

    if not session.app_file_manager.is_notebook_named:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail="File must have a name before exporting",
        )

    # If we have already exported to HTML, don't do it again
    if not session_view.needs_export("html"):
        LOGGER.debug("Already auto-exported to HTML")
        return PlainTextResponse(status_code=HTTPStatus.NOT_MODIFIED)

    # If session_view is empty (no outputs), don't export
    if session_view.is_empty():
        LOGGER.info("No outputs to export")
        return PlainTextResponse(status_code=HTTPStatus.NOT_MODIFIED)

    html, _filename = Exporter().export_as_html(
        app=session.app_file_manager.app,
        filename=session.app_file_manager.filename,
        session_view=session_view,
        display_config=session.config_manager.get_config()["display"],
        request=body,
    )

    # Save the HTML file to disk, at `.marimo/<filename>.html`
    await auto_exporter.save_html(
        filename=session.app_file_manager.filename,
        html=html,
    )
    session_view.mark_auto_export_html()

    return SuccessResponse()


@router.post("/script")
@requires("edit")
async def export_as_script(
    *,
    request: Request,
) -> PlainTextResponse:
    """
    requestBody:
        content:
            application/json:
                schema:
                    $ref: "#/components/schemas/ExportAsScriptRequest"
    responses:
        200:
            description: Export the notebook as a script
            content:
                text/plain:
                    schema:
                        type: string
        400:
            description: File must be saved before downloading
    """
    app_state = AppState(request)
    body = await parse_request(request, cls=ExportAsScriptRequest)
    session = app_state.require_current_session()

    python, filename = Exporter().export_as_script(
        app=session.app_file_manager.app,
        filename=session.app_file_manager.filename,
    )

    if body.download:
        headers = {"Content-Disposition": f"attachment; filename={filename}"}
    else:
        headers = {}

    # Download the Script
    return PlainTextResponse(
        content=python,
        headers=headers,
    )


@router.post("/markdown")
@requires("edit")
async def export_as_markdown(
    *,
    request: Request,
) -> PlainTextResponse:
    """
    requestBody:
        content:
            application/json:
                schema:
                    $ref: "#/components/schemas/ExportAsMarkdownRequest"
    responses:
        200:
            description: Export the notebook as a markdown
            content:
                text/plain:
                    schema:
                        type: string
        400:
            description: File must be saved before downloading
    """
    app_state = AppState(request)
    body = await parse_request(request, cls=ExportAsMarkdownRequest)
    app_file_manager = app_state.require_current_session().app_file_manager
    # Reload the file manager to get the latest state
    app_file_manager.reload()

    if not app_file_manager.path:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail="File must be saved before downloading",
        )

    markdown, filename = Exporter().export_as_md(
        notebook=app_file_manager.app.to_ir(),
        filename=app_file_manager.filename,
    )

    if body.download:
        headers = {"Content-Disposition": f"attachment; filename={filename}"}
    else:
        headers = {}

    # Download the Markdown
    return PlainTextResponse(
        content=markdown,
        headers=headers,
    )


@router.post("/auto_export/markdown")
@requires("edit")
async def auto_export_as_markdown(
    *,
    request: Request,
) -> SuccessResponse | PlainTextResponse:
    """
    requestBody:
        content:
            application/json:
                schema:
                    $ref: "#/components/schemas/ExportAsMarkdownRequest"
    responses:
        200:
            description: Export the notebook as a markdown
            content:
                application/json:
                    schema:
                        $ref: "#/components/schemas/SuccessResponse"
        400:
            description: File must be saved before downloading
    """
    app_state = AppState(request)
    session = app_state.require_current_session()
    session_view = session.session_view

    if not session.app_file_manager.is_notebook_named:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail="File must have a name before exporting",
        )

    # If we have already exported to Markdown, don't do it again
    if not session_view.needs_export("md"):
        LOGGER.debug("Already auto-exported to Markdown")
        return PlainTextResponse(status_code=HTTPStatus.NOT_MODIFIED)

    # Reload the file manager to get the latest state
    session.app_file_manager.reload()

    markdown, _filename = Exporter().export_as_md(
        notebook=session.app_file_manager.app.to_ir(),
        filename=session.app_file_manager.filename,
    )

    # Save the Markdown file to disk, at `.marimo/<filename>.md`
    await auto_exporter.save_md(
        filename=session.app_file_manager.filename,
        markdown=markdown,
    )
    session_view.mark_auto_export_md()

    return SuccessResponse()


@router.post("/auto_export/ipynb")
@requires("edit")
async def auto_export_as_ipynb(
    *,
    request: Request,
) -> SuccessResponse | PlainTextResponse:
    """
    requestBody:
        content:
            application/json:
                schema:
                    $ref: "#/components/schemas/ExportAsIPYNBRequest"
    responses:
        200:
            description: Export the notebook as IPYNB
            content:
                application/json:
                    schema:
                        $ref: "#/components/schemas/SuccessResponse"
        400:
            description: File must be saved before downloading
    """
    app_state = AppState(request)
    session = app_state.require_current_session()
    session_view = session.session_view

    if not session.app_file_manager.is_notebook_named:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail="File must have a name before exporting",
        )

    # If we have already exported to IPYNB, don't do it again
    if not session_view.needs_export("ipynb"):
        LOGGER.debug("Already auto-exported to IPYNB")
        return PlainTextResponse(status_code=HTTPStatus.NOT_MODIFIED)

    # Reload the file manager to get the latest state
    session.app_file_manager.reload()

    ipynb, _filename = Exporter().export_as_ipynb(
        app=session.app_file_manager.app,
        filename=session.app_file_manager.filename,
        sort_mode="top-down",
        session_view=session_view,
    )

    # Save the IPYNB file to disk, at `.marimo/<filename>.ipynb`
    await auto_exporter.save_ipynb(
        filename=session.app_file_manager.filename,
        ipynb=ipynb,
    )
    session_view.mark_auto_export_ipynb()

    return SuccessResponse()
