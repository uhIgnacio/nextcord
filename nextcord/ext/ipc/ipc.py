from typing import overload
from aiohttp import ClientSession, web, client_exceptions
from asyncio import Event, Future, get_event_loop
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
        self.connections = []
        self._app = None
        self._dispatchers = {}
        self._temporary_request_listeners = {}
        self.event_listeners = {}
        self.connected_event = Event()
        self._session = ClientSession()
        self._labels = []
        self.loop = get_event_loop()
        
        if url is not None and host is not None:
            raise TypeError("You can only specify one of url and host.")
        if url is not None:
            self._authority = "worker"
        elif host is not None:
            self._authority = "master"
        else:
            self._authority = "dynamic"

        self.register_listener(self.on_ipc_setlabels, "ipc_setlabels")

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
                self._authority = "master"
                self.connected_event.set()
                return
        else:
            # Successfully connected
            self._authority = "worker"
            self.connected_event.set()
                

    async def _discover(self):
        taken_ports = []
        for port in range(IpcClient.autoconnect_port_range[0], IpcClient.autoconnect_port_range[1]):
            try:
                connection = await self._session.ws_connect(f"ws://localhost:{port}/nextcord-ipc")
            except client_exceptions.ClientConnectorError:
                continue
            except client_exceptions.WSServerHandshakeError:
                # Server exists but it wasnt a websocket server there.
                taken_ports.append(port)
                continue
            await connection.send_json({"type": "auth", "data": self._secret_key})
            self._ws = connection
            self.loop.create_task(self.client_receive_loop())
            return
        return taken_ports


    async def _on_request(self, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)

        login_packet = loads((await ws.receive()).data)
        if login_packet["type"] != "auth":
            await ws.send_json({"type": "auth", "ok": False, "message": "Sent non-auth packet before authenticating"})
            return
        if login_packet["data"] != self._secret_key:
            await ws.send_json({"type": "auth", "ok": False, "message": "Bad token"})
            return
        else:
            await ws.send_json({"type": "auth", "ok": True})
        connection = Connection(ws, login_packet.get("labels", []))
        self.connections.append(connection)
        async for message in ws:
            data = loads(message.data)
            print(data)
            parsed_message = IpcMessage(data, self, connection=connection)
            self.dispatch(parsed_message)

    async def send_raw_message(self, event, message, target=None, *, response_id=None, request_id=None):
        payload = {
            "type": event,
            "data": message,
            "target": target,
            "response_id": response_id,
            "request_id": request_id,
            "from": self.labels[0] if len(self.labels) > 0 else None
        }
        print(f"Sending {payload}")
        if self._authority == "master":
            if target is None:
                # Its a broadcast
                for connection in self.connections:
                    await connection.send_raw(payload)
                self.dispatch(IpcMessage(payload, self))
            elif target == "master":
                self.dispatch(IpcMessage(payload, self))
            else:
                # Directed at spesific targets
                connections = self.get_connections_by_label(target)
                if len(connections) == 0:
                    raise ValueError(f"No connections are connected with the label \"{target}\"")
                for connection in connections:
                    await connection.send_raw(payload)
        else:
            await self._ws.send_json(payload)
    async def request(self, event, message, target=None):
        request_id = uuid4().hex
        future = self.register_response(request_id)
        await self.send_raw_message(event, message, target, request_id=request_id)
        return await future
    async def request_many(self, event, message, target=None):
        connections_with_label = await self.request("ipc_query_label", {"label": target}, "master")
        label_count = connections_with_label["count"]

        if label_count == 0:
            raise ValueError("There is no receivers for this label")

        request_id = uuid4().hex
        future = self.register_response(request_id, label_count)
        return await future

    async def send_message(self, event, message, target=None):
        await self.send_raw_message(event, message, target)

    def get_connections_by_label(self, label):
        connections = []
        for connection in self.connections:
            if label in connection.labels:
                connections.append(connection)
        return connections

    def register_response(self, response_id, response_count=None):
        listeners = self._temporary_request_listeners
        future = Future()
        if response_count is None:
            listeners[response_id] = future
        else:
            listeners[response_id] = [future, response_count, []]
        return future
    def dispatch(self, message):
        response_id = message._raw_data.get("response_id")
        if response_id is not None:
            future = self._temporary_request_listeners.get(response_id, None)
            if future is None:
                print("Response was received for non-request message")
            else:
                future.set_result(message)
        self._dispatch_list(self.event_listeners.get("receive"), [])
        self._dispatch_list(message, self.event_listeners.get(message._raw_data["type"], []))
        
    def _dispatch_list(self, message, targets):
        for target in targets:
            if isinstance(target, list):
                # Has more than one use
                target[2].append(message)
                target[1] -= 1 # Reduce the amount of uses
                if len(target) <= 0:
                    target[0].set_result(target[2])
            else:
                self.loop.create_task(target(message))

    def register_listener(self, listener, event=None):
        if event is None:
            func_name = listener.__name__
            if not func_name.startswith("on_"):
                raise ValueError("Event listener function names have to start with on_")
            event = func_name[2:]
        if event not in self.event_listeners.keys():
            self.event_listeners[event] = []
        self.event_listeners[event].append(listener)

    @property
    def labels(self):
        return self._labels
    
    async def set_labels(self, labels: list[str]):
        await self.send_message("ipc_setlabels", labels, "master")
        self._labels = labels

    async def add_labels(self, *labels: str):
        await self.set_labels([*labels, *self.labels])

    async def on_ipc_setlabels(self, message):
        message._connection.labels = message.payload

    async def client_receive_loop(self):
        async for message in self._ws:
            data = loads(message.data)
            self.dispatch(IpcMessage(data, self))

        



class IpcMessage():
    def __init__(self, data, ipc_client, *, connection=None):
        self.payload = data.get("data")
        
        # Internal data
        self._raw_data = data
        self._ipc = ipc_client
        self._has_responded = False
        self._connection = connection

    async def respond(self, message, *, request_response=False):
        if (response_id := self._raw_data.get("request_id")) is None:
            raise ValueError("Can't respond to a non-respondable message")
        if request_response:
            request_id = uuid4().hex
            response = self._ipc.register_response(request_id)
        else:
            request_id = None
        await self._ipc.send_raw_message(None, message, self._raw_data["from"], response_id=response_id, request_id=request_id)
        if request_response:
            return await response

    @property
    def respondable(self):
        return bool(self._raw_data.get("request_id")) and not self._has_responded

    def __getitem__(self, key: str):
        return self.payload[key]

        
class Connection:
    def __init__(self, websocket, labels):
        self._ws = websocket
        self.labels = labels

    async def send_raw(self, data):
        await self._ws.send_json(data)
