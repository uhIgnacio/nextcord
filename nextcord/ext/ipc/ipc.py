from typing import overload
from tempfile import gettempdir
from pathlib import Path
from aiohttp import ClientSession

class IpcClient():
    @overload
    def __init__(self, secret_key: str):
        ...
    @overload
    def __init__(self, secret_key: str, *, url: str):
        ...
    @overload
    def __init__(self, secret_key: str, *, host: str):
        ...

    def __init__(self, secret_key: str, *, url: str = None, host: str = None):
        self._secret_key: str = secret_key
        self._url = url
        self._host = host
        self._session = ClientSession()
        
        if url is not None and host is not None:
            raise TypeError("You can only specify one of url and host.")
        if url is not None:
            self._connection_mode = "remote"
            self._authority = "worker"
        elif host is not None:
            self._connection_mode = "remote"
            self._authority = "master"
        else:
            self._connection_mode = "unix"
            self._authority = "dynamic"
            # TODO: Discover authority

    async def connect(self):
        if self._connection_mode == "unix":
            await self._connect_unix()

    async def _connect_unix(self):
        tempdir = Path(gettempdir())

    async def _scan_unix(self, tempdir):
        for ipc_file in tempdir.glob("nextcord-ipc-*"):
            url = "unix://" + str(ipc_file)
            print(url)
            connection = await self._session.ws_connect(url)
            await connection.send_json({"type": "auth", "data": {"secret_key": self._secret_key}})
            


        

