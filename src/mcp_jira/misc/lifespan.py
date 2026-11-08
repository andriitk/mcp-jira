from typing import Any, Self

from dependency_injector.containers import Container


class Lifespan:
    """Starlette lifespan: initialise container resources on startup, release them on shutdown.

    https://python-dependency-injector.ets-labs.org/providers/resource.html
    """

    def __init__(self, container: Container) -> None:
        self.container = container

    async def __aenter__(self) -> dict:
        result = self.container.init_resources()
        if result is not None:
            await result
        return {}

    async def __aexit__(self, *exc_info: Any) -> None:
        result = self.container.shutdown_resources()
        if result is not None:
            await result

    def __call__(self, *args: Any, **kwargs: Any) -> Self:
        return self
