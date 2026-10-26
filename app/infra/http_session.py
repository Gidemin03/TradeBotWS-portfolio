import aiohttp


class HttpSessionFactory:
    """
    Одна общая aiohttp.ClientSession на всё приложение.
    """

    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None

    async def get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()