"""Owned private Comfy socket for the statically defined I2V graph only."""
import os
import socket

from aiohttp import web
import main


loop, prompt_server, start_all = main.start_comfyui()
token = os.environ.pop("NGS_I2V_LOCAL_TOKEN")


@web.middleware
async def require_worker(request, handler):
    if request.headers.get("Authorization") != "Bearer " + token:
        raise web.HTTPUnauthorized()
    return await handler(request)


prompt_server.app.middlewares.insert(0, require_worker)


async def own_loopback(addresses, call_on_start=None, verbose=True):
    if addresses != [("127.0.0.1", 0)] or call_on_start is not None:
        raise ValueError("I2V_LISTEN_SCOPE")
    runner = web.AppRunner(prompt_server.app, access_log=None)
    await runner.setup()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        await web.SockSite(runner, sock).start()
        print(f"NGS_OWNED_I2V_READY {port}", flush=True)
    except BaseException:
        sock.close()
        await runner.cleanup()
        raise


prompt_server.start_multi_address = own_loopback
try:
    loop.run_until_complete(start_all())
finally:
    main.app.agent_host.stop()
    prompt_server.asset_manager.shutdown()
