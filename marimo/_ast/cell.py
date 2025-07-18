# Copyright 2024 Marimo. All rights reserved.
from __future__ import annotations

import ast
import dataclasses
import inspect
import os
from collections.abc import Awaitable, Mapping
from typing import TYPE_CHECKING, Any, Literal, Optional

from marimo import _loggers
from marimo._ast.sql_visitor import SQLVisitor
from marimo._ast.visitor import ImportData, Language, Name, VariableData
from marimo._runtime.exceptions import MarimoRuntimeException
from marimo._types.ids import CellId_t
from marimo._utils.deep_merge import deep_merge

LOGGER = _loggers.marimo_logger()

if TYPE_CHECKING:
    from collections.abc import Iterable
    from types import CodeType

    from marimo._ast.app import InternalApp
    from marimo._messaging.types import Stream
    from marimo._output.hypertext import Html


@dataclasses.dataclass
class CellConfig:
    """
    Internal representation of a cell's configuration.
    This is not part of the public API.
    """

    column: Optional[int] = None

    # If True, the cell and its descendants cannot be executed,
    # but they can still be added to the graph.
    disabled: bool = False

    # If True, the cell is hidden from the editor.
    hide_code: bool = False

    @classmethod
    def from_dict(
        cls, kwargs: dict[str, Any], warn: bool = True
    ) -> CellConfig:
        config = cls(
            **{k: v for k, v in kwargs.items() if k in CellConfigKeys}
        )
        if warn and (invalid := set(kwargs.keys()) - CellConfigKeys):
            LOGGER.warning(f"Invalid config keys: {invalid}")
        return config

    def asdict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    def asdict_without_defaults(self) -> dict[str, Any]:
        return {
            k: v
            for k, v in self.asdict().items()
            if v != getattr(CellConfig(), k)
        }

    def is_different_from_default(self) -> bool:
        return self != CellConfig()

    def configure(self, update: dict[str, Any] | CellConfig) -> None:
        """Update the config in-place.

        `update` can be a partial config or a CellConfig
        """
        if isinstance(update, CellConfig):
            update = dataclasses.asdict(update)
        new_config = dataclasses.asdict(
            CellConfig.from_dict(deep_merge(dataclasses.asdict(self), update))
        )
        for key, value in new_config.items():
            self.__setattr__(key, value)


CellConfigKeys = frozenset(
    {field.name for field in dataclasses.fields(CellConfig)}
)


# States in a cell's runtime state machine
#
# idle: cell has run with latest inputs
# queued: cell is queued to run
# running: cell is running
# disabled-transitively: cell is disabled because a parent is disabled
RuntimeStateType = Literal[
    "idle", "queued", "running", "disabled-transitively"
]


@dataclasses.dataclass
class RuntimeState:
    state: Optional[RuntimeStateType] = None


# Statuses for a cell's attempted execution
#
# cancelled:    an ancestor raised an exception
# marimo-error: cell was prevented from executing
# disabled:     skipped because the cell is disabled
RunResultStatusType = Literal[
    "success",
    "exception",
    "cancelled",
    "interrupted",
    "marimo-error",
    "disabled",
]


@dataclasses.dataclass
class RunResultStatus:
    state: Optional[RunResultStatusType] = None


@dataclasses.dataclass
class ImportWorkspace:
    """A workspace for runtimes to use to manage a cell's imports."""

    # A cell is an import block if all statements are import statements
    is_import_block: bool = False
    # Defs that have been imported by the runtime
    imported_defs: set[Name] = dataclasses.field(default_factory=set)


def _is_coroutine(code: Optional[CodeType]) -> bool:
    if code is None:
        return False
    return inspect.CO_COROUTINE & code.co_flags == inspect.CO_COROUTINE


@dataclasses.dataclass
class CellStaleState:
    state: bool = False


@dataclasses.dataclass
class CellOutput:
    output: Any = None


@dataclasses.dataclass
class ParsedSQLStatements:
    parsed: Optional[list[str]] = None


@dataclasses.dataclass(frozen=True)
class CellImpl:
    # hash of code
    key: int
    code: str
    mod: ast.Module
    defs: set[Name]
    refs: set[Name]
    # Variables that should only live for the duration of the cell
    temporaries: set[Name]

    # metadata about definitions
    variable_data: dict[Name, list[VariableData]]
    deleted_refs: set[Name]
    body: Optional[CodeType]
    last_expr: Optional[CodeType]
    # whether this cell is Python or SQL
    language: Language
    # unique id
    cell_id: CellId_t

    # Mutable fields
    # explicit configuration of cell
    config: CellConfig = dataclasses.field(default_factory=CellConfig)
    # workspace for runtimes to use to store metadata about imports
    import_workspace: ImportWorkspace = dataclasses.field(
        default_factory=ImportWorkspace
    )
    # execution status, inferred at runtime
    _status: RuntimeState = dataclasses.field(default_factory=RuntimeState)
    _run_result_status: RunResultStatus = dataclasses.field(
        default_factory=RunResultStatus
    )
    # whether the cell is stale, inferred at runtime
    _stale: CellStaleState = dataclasses.field(default_factory=CellStaleState)
    # cells can optionally hold a reference to their output
    _output: CellOutput = dataclasses.field(default_factory=CellOutput)
    # parsed sql statements
    _sqls: ParsedSQLStatements = dataclasses.field(
        default_factory=ParsedSQLStatements
    )
    _raw_sqls: ParsedSQLStatements = dataclasses.field(
        default_factory=ParsedSQLStatements
    )
    # Whether this cell can be executed as a test cell.
    _test: bool = False

    def configure(self, update: dict[str, Any] | CellConfig) -> CellImpl:
        """Update the cell config.

        `update` can be a partial config.
        """
        self.config.configure(update)
        return self

    @property
    def runtime_state(self) -> Optional[RuntimeStateType]:
        """Gets the current runtime state of the cell.

        Returns:
            Optional[RuntimeStateType]: The current state, one of:
                - "idle": cell has run with latest inputs
                - "queued": cell is queued to run
                - "running": cell is running
                - "disabled-transitively": cell is disabled because a parent is disabled
                - None: state not set
        """
        return self._status.state

    @property
    def run_result_status(self) -> Optional[RunResultStatusType]:
        return self._run_result_status.state

    def _get_sqls(self, raw: bool = False) -> list[str]:
        try:
            visitor = SQLVisitor(raw=raw)
            visitor.visit(ast.parse(self.code))
            return visitor.get_sqls()
        except Exception:
            return []

    @property
    def sqls(self) -> list[str]:
        """Returns parsed SQL statements from this cell.

        Returns:
            list[str]: List of SQL statement strings parsed from the cell code.
        """
        if self._sqls.parsed is not None:
            return self._sqls.parsed

        self._sqls.parsed = self._get_sqls()
        return self._sqls.parsed

    @property
    def raw_sqls(self) -> list[str]:
        """Returns unparsed SQL statements from this cell.

        Returns:
            list[str]: List of SQL statements verbatim from the cell code.
        """
        if self._raw_sqls.parsed is not None:
            return self._raw_sqls.parsed

        self._raw_sqls.parsed = self._get_sqls(raw=True)
        return self._raw_sqls.parsed

    @property
    def stale(self) -> bool:
        return self._stale.state

    @property
    def disabled_transitively(self) -> bool:
        return self.runtime_state == "disabled-transitively"

    @property
    def imports(self) -> Iterable[ImportData]:
        """Return a set of import data for this cell."""
        import_data = []
        for data in self.variable_data.values():
            import_data.extend(
                [
                    datum.import_data
                    for datum in data
                    if datum.import_data is not None
                ]
            )
        return import_data

    @property
    def imported_namespaces(self) -> set[Name]:
        """Return a set of the namespaces imported by this cell."""
        return set(
            import_data.module.split(".")[0] for import_data in self.imports
        )

    def namespace_to_variable(self, namespace: str) -> Name | None:
        """Returns the variable name corresponding to an imported namespace

        Relevant for imports "as" imports, eg

        import matplotlib.pyplot as plt

        In this case the namespace is "matplotlib" but the name is "plt".
        """
        for import_data in self.imports:
            if import_data.namespace == namespace:
                return import_data.definition
        return None

    def is_coroutine(self) -> bool:
        return _is_coroutine(self.body) or _is_coroutine(self.last_expr)

    @property
    def toplevel_variable(self) -> Optional[VariableData]:
        """Return the single, scoped, toplevel variable defined if found."""
        try:
            tree = ast.parse(self.code)
        except SyntaxError:
            return None

        if len(self.defs) != 1:
            return None

        if not (
            len(tree.body) == 1
            and isinstance(
                tree.body[0],
                (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
            )
        ):
            return None

        # Check that def matches the single definition
        name = tree.body[0].name
        if not (name == list(self.defs)[0] and name in self.variable_data):
            return None

        if len(variable_data := self.variable_data[name]) != 1:
            return None

        return list(variable_data)[0]

    @property
    def init_variable_data(self) -> dict[Name, VariableData]:
        return {key: vs[0] for key, vs in self.variable_data.items()}

    def set_runtime_state(
        self, status: RuntimeStateType, stream: Stream | None = None
    ) -> None:
        """Sets the cell's execution status and broadcasts to frontends.

        Args:
            status (RuntimeStateType): New runtime state to set
            stream (Stream | None, optional): Stream to broadcast on. Defaults to None.
        """
        from marimo._messaging.ops import CellOp
        from marimo._runtime.context import (
            ContextNotInitializedError,
            get_context,
        )

        self._status.state = status
        try:
            get_context()
        except ContextNotInitializedError:
            return

        assert self.cell_id is not None
        CellOp.broadcast_status(
            cell_id=self.cell_id, status=status, stream=stream
        )

    def set_run_result_status(
        self, run_result_status: RunResultStatusType
    ) -> None:
        self._run_result_status.state = run_result_status

    def set_stale(
        self, stale: bool, stream: Stream | None = None, broadcast: bool = True
    ) -> None:
        from marimo._messaging.ops import CellOp

        self._stale.state = stale
        if broadcast:
            CellOp.broadcast_stale(
                cell_id=self.cell_id, stale=stale, stream=stream
            )

    def set_output(self, output: Any) -> None:
        self._output.output = output

    @property
    def output(self) -> Any:
        return self._output.output


@dataclasses.dataclass
class Cell:
    """An executable notebook cell

    Cells are the fundamental unit of execution in marimo. They represent
    a single unit of execution, which can be run independently and reused
    across notebooks.

    Cells are defined using the `@app.cell` decorator, which registers the
    function as a cell in marimo.

    For example:

    ```python
    @app.cell
    def my_cell(mo, x, y):
        z = x + y
        mo.md(f"The value of z is {z}")  # This will output markdown
        return (z,)
    ```

    Cells can be invoked as functions, and picked up by external frameworks
    (like `pytest` if their name starts with `test_`). However, consider
    implementing reusable functions (@app.function) in your notebook for
    granular control of the output.

    A `Cell` object can also be executed without arguments via its `run()`
    method, which returns the cell's last expression (output) and a mapping from
    its defined names to its values.

    For example:

    ```python
    from my_notebook import my_cell

    output, definitions = my_cell.run()
    ```

    Cells can be named via the marimo editor in the browser, or by
    changing the cell's function name in the notebook file.

    See the documentation of `run` for info and examples.
    """

    # Function from which this cell was created
    _name: str

    # Internal cell representation
    _cell: CellImpl

    # App to which this cell belongs
    _app: InternalApp | None = None

    # Number of reserved arguments for pytest
    _pytest_reserved: set[str] = dataclasses.field(default_factory=set)

    # Whether to expose this cell as a test cell.
    _test_allowed: bool = False

    _expected_signature: Optional[tuple[str, ...]] = None

    @property
    def name(self) -> str:
        return self._name

    @property
    def refs(self) -> set[str]:
        """The references that this cell takes as input"""
        return self._cell.refs

    @property
    def defs(self) -> set[str]:
        """The definitions made by this cell"""
        return self._cell.defs

    @property
    def _is_coroutine(self) -> bool:
        """Whether this cell is a coroutine function.

        If True, then this cell's `run` method returns an awaitable.
        """
        if hasattr(self, "_is_coro_cached"):
            return self._is_coro_cached
        assert self._app is not None
        self._is_coro_cached: bool = self._app.runner.is_coroutine(
            self._cell.cell_id
        )
        return self._is_coro_cached

    def _help(self) -> Html:
        from marimo._output.formatting import as_html
        from marimo._output.md import md

        signature_prefix = "Async " if self._is_coroutine else ""
        execute_str_refs = (
            f"output, defs = await {self.name}.run(**refs)"
            if self._is_coroutine
            else f"output, defs = {self.name}.run(**refs)"
        )
        execute_str_no_refs = (
            f"output, defs = await {self.name}.run()"
            if self._is_coroutine
            else f"output, defs = {self.name}.run()"
        )

        return md(
            f"""
            **{signature_prefix}Cell `{self.name}`**

            You can execute this cell using

            `{execute_str_refs}`

            where `refs` is a dictionary mapping a subset of the
            cell's references to values. Missing refs will be automatically
            computed. To automatically compute all refs, simply run with

            `{execute_str_no_refs}`

            **References:**

            {as_html(list(self.refs))}

            **Definitions:**

            {as_html(list(self.defs))}
            """
        )

    # The property __test__ is picked up by nose and pytest.
    # We have the compiler mark if the cell name starts with test_
    # _or_, is comprised of only tests; allowing for testing suites to
    # collect this cell.
    @property
    def __test__(self) -> bool:
        return self._test_allowed

    def _register_app(self, app: InternalApp) -> None:
        self._app = app

    def run(
        self, **refs: Any
    ) -> (
        tuple[Any, Mapping[str, Any]]
        | Awaitable[tuple[Any, Mapping[str, Any]]]
    ):
        """
        Run this cell and return its visual output and definitions.

        Use this method to run **named cells** and retrieve their output and
        definitions. This lets you reuse cells defined in one notebook in another
        notebook or Python file. It also makes it possible to write and execute
        unit tests for notebook cells using a test framework like `pytest`.

        Examples:
            marimo cells can be given names either through the editor cell menu
            or by manually changing the function name in the notebook file. For
            example, consider a notebook `notebook.py`:

            ```python
            import marimo

            app = marimo.App()


            @app.cell
            def __():
                import marimo as mo

                return (mo,)


            @app.cell
            def __():
                x = 0
                y = 1
                return (x, y)


            @app.cell
            def add(mo, x, y):
                z = x + y
                mo.md(f"The value of z is {z}")
                return (z,)


            if __name__ == "__main__":
                app.run()
            ```

            To reuse the `add` cell in another notebook, you'd simply write:

            ```python
            from notebook import add

            # `output` is the markdown rendered by `add`
            # defs["z"] == `1`
            output, defs = add.run()
            ```

            When `run` is called without arguments, it automatically computes
            the values that the cell depends on (in this case, `mo`, `x`, and
            `y`). You can override these values by providing any subset of them
            as keyword arguments. For example,

            ```python
            # defs["z"] == 4
            output, defs = add.run(x=2, y=2)
            ```

        Defined UI Elements:
            If the cell's `output` has UI elements that are in `defs`, interacting
            with the output in the frontend will trigger reactive execution of
            cells that reference the `defs` object. For example, if `output` has
            a slider defined by the cell, then scrubbing the slider will cause
            cells that reference `defs` to run.

        Async cells:
            If this cell is a coroutine function (starting with `async`), or if
            any of its ancestors are coroutine functions, then you'll need to
            `await` the result: `output, defs = await cell.run()`. You can check
            whether the result is an awaitable using:

            ```python
            from collections.abc import Awaitable

            ret = cell.run()
            if isinstance(ret, Awaitable):
                output, defs = await ret
            else:
                output, defs = ret
            ```

        Args:
            **refs (Any):
                You may pass values for any of this cell's references as keyword
                arguments. marimo will automatically compute values for any refs
                that are not provided by executing the parent cells that compute
                them.

        Returns:
            tuple `(output, defs)`, or an awaitable of the same:
                `output` is the cell's last expression and `defs` is a `Mapping`
                from the cell's defined names to their values.
        """
        assert self._app is not None

        # Inject setup cell definitions so that we do not rerun the setup cell.
        # With an exception for tests that should act as if it's in runtime.
        if "PYTEST_CURRENT_TEST" not in os.environ:
            if self._app._app._setup is not None:
                from_setup = {
                    k: v
                    for k, v in self._app._app._setup._glbls.items()
                    if k in self._cell.refs
                }
                refs = {**from_setup, **refs}

        try:
            if self._is_coroutine:
                return self._app.run_cell_async(cell=self, kwargs=refs)
            else:
                return self._app.run_cell_sync(cell=self, kwargs=refs)
        except MarimoRuntimeException as e:
            raise e.__cause__ from None  # type: ignore

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        from marimo._ast.toplevel import TopLevelExtraction

        assert self._app is not None
        # Definitions on a module level are not part of the signature, and as
        # such, should not be provided with the call.

        # NB. TopLevelExtraction assumes that all cells that can be exposed will
        # be, but signature provides context for what is actually scoped.
        allowed_refs = TopLevelExtraction.from_app(self._app).allowed_refs
        allowed_refs -= set(self._expected_signature or ())

        arg_names = sorted((self._cell.refs - allowed_refs) - self._cell.defs)
        argc = len(arg_names)

        call_args = {name: arg for name, arg in zip(arg_names, args)}
        call_args.update(kwargs)
        call_argc = len(call_args.keys()) + len(self._pytest_reserved)

        if unexpected := call_args.keys() - arg_names:
            raise TypeError(
                f"{self.name}() got an unexpected argument(s) '{unexpected}'"
            )

        is_pytest = (
            "PYTEST_CURRENT_TEST" in os.environ
            or "MARIMO_PYTEST_WASM" in os.environ
        )
        # Capture pytest case, where arguments don't match the references.
        if self._pytest_reserved - set(arg_names):
            raise TypeError(
                "A mismatch in expected argument names likely means you should "
                "resave the notebook in the marimo editor."
            )

        actual_count = len(args) + len(kwargs)

        mismatch_context = ""
        if self._expected_signature is not None:
            if tuple(arg_names) != self._expected_signature:
                mismatch_context = (
                    f"The signature of function ``{self._name}'': {self._expected_signature} "
                    f"does not match the expected signature: {tuple(arg_names)}. "
                    "A mismatch in arguments likely means you should "
                    "resave the notebook in the marimo editor."
                )

        # If all the arguments are provided, then run as if it were a normal
        # function call. An incorrect number of arguments will raise a
        # TypeError (the same as a normal function call).
        #
        # pytest is an exception here, since it enables testing directly on
        # notebooks, and the graph will be executed if needed.
        if argc == call_argc and (
            is_pytest
            or (
                all(name in call_args for name in arg_names)
                and argc == actual_count
            )
        ):
            # Function invoked successfully, but let the user know there is a
            # mismatch in the signature.
            if mismatch_context:
                LOGGER.warning(mismatch_context)
            # Note, run returns a tuple of (output, defs)-
            # so stripped defs is required.
            ret = self.run(**call_args)
            if isinstance(ret, Awaitable):

                async def await_and_return() -> Any:
                    output, _ = await ret
                    return output

                return await_and_return()
            else:
                output, _ = ret
            return output

        if is_pytest:
            call_str = mismatch_context
        else:
            await_str = "await " if self._is_coroutine else ""
            mismatch_context += " Alternatively; " if mismatch_context else ""
            call_str = (
                f"{mismatch_context}Consider calling with `outputs, defs = "
                f"{await_str}{self.name}.run()`."
            )

        were = "were" if actual_count != 1 else "was"
        raise TypeError(
            f"{self.name}() takes {argc} positional arguments but "
            f"{actual_count} {were} given. {call_str}"
        )


@dataclasses.dataclass
class SourcePosition:
    filename: str
    lineno: int
    col_offset: int
