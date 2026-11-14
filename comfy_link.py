"""Talking to ComfyUI's own queue from inside ComfyUI: send a graph, see what is running,
read a finished job, and stop one. Used for MOSS-Image renders (and for following any render)."""

import json


def comfy_base():
    """The address this ComfyUI listens on, as seen from inside it."""
    from comfy.cli_args import args

    host = (args.listen or "127.0.0.1").split(",")[0].strip()
    if host in ("", "0.0.0.0", "::"):
        host = "127.0.0.1"
    if ":" in host:
        host = "[%s]" % host
    scheme = "https" if getattr(args, "tls_keyfile", None) and getattr(args, "tls_certfile", None) else "http"
    return "%s://%s:%s" % (scheme, host, args.port)


async def comfy(method, path, body=None):
    import aiohttp

    async with aiohttp.ClientSession() as session:
        async with session.request(method, comfy_base() + path, json=body, ssl=False) as response:
            text = await response.text()
            try:
                data = json.loads(text) if text else None
            except ValueError:
                data = {"error": {"message": text[:300]}}
            return response.status, data


class Refused(Exception):
    """ComfyUI didn't accept a graph. The message says why, in ComfyUI's words."""


def _why(data):
    error = (data or {}).get("error") or {}
    parts = [error.get("message") or "ComfyUI refused the job."]
    for node_id, node in ((data or {}).get("node_errors") or {}).items():
        for item in node.get("errors", [])[:2]:
            parts.append("%s (%s): %s" % (node.get("class_type", "node"), node_id, item.get("details") or item.get("message")))
    return " ".join(parts)


async def queue_prompt(graph, extra_data=None, client_id=None):
    """Put a graph in ComfyUI's queue. Returns the prompt id."""
    payload = {"prompt": graph, "extra_data": extra_data or {}}
    if client_id:
        payload["client_id"] = client_id
    status, data = await comfy("POST", "/prompt", payload)
    if status != 200 or not (data or {}).get("prompt_id"):
        raise Refused(_why(data))
    return data["prompt_id"]


def queue_state():
    """The prompt ids ComfyUI is running now, and those waiting."""
    from server import PromptServer

    running, queued = PromptServer.instance.prompt_queue.get_current_queue()
    return {entry[1] for entry in running}, {entry[1] for entry in queued}


def history(prompt_id):
    from server import PromptServer

    return PromptServer.instance.prompt_queue.get_history(prompt_id=prompt_id).get(prompt_id)


def failure(entry):
    """Why a finished job has no result, in plain words."""
    if not entry:
        return "ComfyUI lost this render (it may have been restarted)."
    for kind, info in (entry.get("status") or {}).get("messages") or []:
        if kind == "execution_error":
            return "%s: %s" % (info.get("node_type") or "A node", info.get("exception_message") or "it failed")
        if kind == "execution_interrupted":
            return "The render was stopped."
    return "The render finished without saving a file."


async def cancel(prompt_id):
    """Stop a render: interrupt it if it is running, or take it out of the queue."""
    running, _ = queue_state()
    if prompt_id in running:
        await comfy("POST", "/interrupt", {"prompt_id": prompt_id})
    else:
        await comfy("POST", "/queue", {"delete": [prompt_id]})
