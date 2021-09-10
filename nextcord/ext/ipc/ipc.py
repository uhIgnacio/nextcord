from typing import overload
from tempfile import gettempdir
from pathlib import Path
from aiohttp import ClientSession, web, UnixConnector, client_exceptions
from uuid import uuid4
try:
    from orjson import loads
except:
    from json import loads

class IpcClient():
    autoconnect_port_range = (46000, 46100)

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
        self._ws = None # TODO: Typehint
        self._app = None
        
        if url is not None and host is not None:
            raise TypeError("You can only specify one of url and host.")
        if url is not None:
            self._authority = "worker"
        elif host is not None:
            self._authority = "master"
        else:
            self._authority = "dynamic"
            # TODO: Discover authority

    async def connect(self):
        discovery_result = await self._discover()
        if isinstance(discovery_result, list):
            # No result was found. Result is a list of already taken port numbers
            for port in range(IpcClient.autoconnect_port_range[0], IpcClient.autoconnect_port_range[1]):
                if port in discovery_result:
                    continue
                self._app = web.Application()
                self._app.router.add_route("GET", "/nextcord-ipc", self._on_request)

                runner = web.AppRunner(self._app)
                await runner.setup()

                site = web.TCPSite(runner, port=port)
                try:
                    self._site = await site.start()
                except OSError:
                    # Port must have been taken by some other service.
                    continue
        else:
            # Successfully connected
            ...
                

    async def _discover(self):
        taken_ports = []
        async with ClientSession() as session:
            for port in range(IpcClient.autoconnect_port_range[0], IpcClient.autoconnect_port_range[1]):
                try:
                    connection = await session.ws_connect(f"ws://localhost:{port}/nextcord-ipc")
                except client_exceptions.ClientConnectorError:
                    continue
                except client_exceptions.WSServerHandshakeError:
                    # Server exists but it wasnt a websocket server there.
                    taken_ports.append(port)
                    continue
                print(f"Can reach on port {port}")
                await connection.send_json({"type": "auth", "data": self._secret_key})
                self._ws = connection
                return
        return taken_ports


    async def _on_request(self, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        print("IPC user connecting!")

        login_packet = loads((await ws.receive()).data)
        print(login_packet)
        if login_packet["type"] != "auth":
            await ws.send_json({"type": "auth", "ok": False, "message": "Sent non-auth packet before authenticating"})
            return
        if login_packet["data"] != self._secret_key:
            await ws.send_json({"type": "auth", "ok": False, "message": "Bad token"})
            return
        else:
            await ws.send_json({"type": "auth", "ok": True})
        print("IPC user connected!")
        async for message in ws:
            print(message)
            


        

