from __future__ import annotations

import logging
from typing import (
    TYPE_CHECKING,
    Any,
    AsyncIterator,
    Generator,
    Iterator,
    Optional,
    Protocol,
    Tuple,
)

from opentelemetry import trace as trace_api
from opentelemetry.util.types import AttributeValue
from wrapt import ObjectProxy

from openinference.instrumentation.mistralai._utils import _finish_tracing
from openinference.instrumentation.mistralai._with_span import _WithSpan

if TYPE_CHECKING:
    from mistralai.models import CompletionEvent

__all__ = (
    "_Stream",
    "_ResponseAccumulator",
)

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class _ResponseAccumulator(Protocol):
    def process_chunk(self, chunk: Any) -> None: ...

    def get_attributes(self) -> Iterator[Tuple[str, AttributeValue]]: ...

    def get_extra_attributes(self) -> Iterator[Tuple[str, AttributeValue]]: ...


class _Stream(ObjectProxy):  # type: ignore
    __slots__ = (
        "_self_with_span",
        "_self_iteration_count",
        "_self_is_finished",
        "_self_response_accumulator",
    )

    def __init__(
        self,
        stream: Optional[Generator[CompletionEvent, None, None]],
        with_span: _WithSpan,
        response_accumulator: Optional[_ResponseAccumulator] = None,
    ) -> None:
        super().__init__(stream)
        self._self_with_span = with_span
        self._self_iteration_count = 0
        self._self_is_finished = with_span.is_finished
        self._self_response_accumulator = response_accumulator

    def __iter__(self) -> Iterator[Any]:
        return self

    def __next__(self) -> Any:
        # pass through mistaken calls
        if not hasattr(self.__wrapped__, "__next__"):
            self.__wrapped__.__next__()
        try:
            chunk: Any = self.__wrapped__.__next__()
        except Exception as exception:
            if not self._self_is_finished:
                if isinstance(exception, StopIteration):
                    status = trace_api.Status(status_code=trace_api.StatusCode.OK)
                else:
                    status = trace_api.Status(
                        status_code=trace_api.StatusCode.ERROR,
                        # Follow the format in OTEL SDK for description, see:
                        # https://github.com/open-telemetry/opentelemetry-python/blob/2b9dcfc5d853d1c10176937a6bcaade54cda1a31/opentelemetry-api/src/opentelemetry/trace/__init__.py#L588  # noqa E501
                        description=f"{type(exception).__name__}: {exception}",
                    )
                    self._self_with_span.record_exception(exception)
                self._finish_tracing(status=status)
            raise
        else:
            self._process_chunk(chunk)
            return chunk

    def __aiter__(self) -> AsyncIterator[Any]:
        return self

    def _process_chunk(self, chunk: Any) -> None:
        if not self._self_iteration_count:
            try:
                self._self_with_span.add_event("First Token Stream Event")
            except Exception:
                logger.exception("Failed to add event to span")
        self._self_iteration_count += 1
        if self._self_response_accumulator is not None:
            try:
                self._self_response_accumulator.process_chunk(chunk)
            except Exception:
                logger.exception("Failed to accumulate response")

    def _finish_tracing(
        self,
        status: Optional[trace_api.Status] = None,
    ) -> None:
        _finish_tracing(
            status=status,
            with_span=self._self_with_span,
            has_attributes=self,
        )
        self._self_is_finished = True

    def get_attributes(self) -> Iterator[Tuple[str, AttributeValue]]:
        if self._self_response_accumulator is not None:
            yield from self._self_response_accumulator.get_attributes()

    def get_extra_attributes(self) -> Iterator[Tuple[str, AttributeValue]]:
        if self._self_response_accumulator is not None:
            yield from self._self_response_accumulator.get_extra_attributes()


class _AsyncStream(ObjectProxy):  # type: ignore
    __slots__ = (
        "stream",
        "_self_with_span",
        "_self_iteration_count",
        "_self_is_finished",
        "_self_response_accumulator",
    )

    def __init__(
        self,
        stream: Any,
        with_span: _WithSpan,
        response_accumulator: Optional[_ResponseAccumulator] = None,
    ) -> None:
        self.stream = stream
        self._self_with_span = with_span
        self._self_iteration_count = 0
        self._self_is_finished = with_span.is_finished
        self._self_response_accumulator = response_accumulator

    async def stream_async_with_accumulator(self) -> AsyncIterator[Any]:
        async def generator() -> AsyncIterator[Any]:
            try:
                async for event in await self.stream:
                    self._process_chunk(event)
                    if event.data.choices[0].finish_reason is not None:
                        self._finish_tracing()
                    yield event
            except Exception as exception:
                # Handle exceptions that occur during the async for loop
                status = trace_api.Status(
                    status_code=trace_api.StatusCode.ERROR,
                    description=f"{type(exception).__name__}: {exception}",
                )
                self._self_with_span.record_exception(exception)
                self._finish_tracing(status=status)
                raise

        return generator()

    def _process_chunk(self, chunk: Any) -> None:
        if not self._self_iteration_count:
            try:
                self._self_with_span.add_event("First Token Stream Event")
            except Exception:
                logger.exception("Failed to add event to span")
        self._self_iteration_count += 1
        if self._self_response_accumulator is not None:
            try:
                self._self_response_accumulator.process_chunk(chunk)
            except Exception:
                logger.exception("Failed to accumulate response")

    def _finish_tracing(
        self,
        status: Optional[trace_api.Status] = None,
    ) -> None:
        _finish_tracing(
            status=status,
            with_span=self._self_with_span,
            has_attributes=self,
        )
        self._self_is_finished = True

    def get_attributes(self) -> Iterator[Tuple[str, AttributeValue]]:
        if self._self_response_accumulator is not None:
            yield from self._self_response_accumulator.get_attributes()

    def get_extra_attributes(self) -> Iterator[Tuple[str, AttributeValue]]:
        if self._self_response_accumulator is not None:
            yield from self._self_response_accumulator.get_extra_attributes()
