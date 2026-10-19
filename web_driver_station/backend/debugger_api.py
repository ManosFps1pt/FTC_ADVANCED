"""Shared read-only results API and laptop-only benchmark controls."""
from __future__ import annotations

import asyncio
import io
import json

from google.protobuf.json_format import MessageToDict
from .debugger_inputs import encode_inputs
import uuid
import zipfile
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from .debugger_results import DebuggerResults, atomic_json
from .protocol import robot_data_pb2 as wire


def install_result_reads(app: FastAPI, results: DebuggerResults) -> None:
    @app.get("/api/debug/results")
    def list_results():
        return results.list()

    @app.get("/api/debug/results/{run_id}")
    def get_result(run_id: str):
        try: return results.report(run_id)
        except (OSError, ValueError): raise HTTPException(404, "Debugger result not found")

    @app.get("/api/debug/results/{run_id}/report")
    def report_download(run_id: str):
        try:
            results.report(run_id)
            return FileResponse(results.path(run_id)/"analysis.json", filename=f"{run_id}-analysis.json")
        except (OSError, ValueError): raise HTTPException(404, "Report not found")

    @app.get("/api/debug/results/{run_id}/raw")
    def raw_download(run_id: str):
        try:
            results.report(run_id)
            path = results.path(run_id)
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.write(path/"manifest.json", "manifest.json")
                for file in sorted((path/"raw").iterdir()):
                    if file.is_file() and not file.is_symlink(): archive.write(file, f"raw/{file.name}")
            return Response(buffer.getvalue(), media_type="application/zip",
                            headers={"Content-Disposition": f'attachment; filename="{run_id}-raw.zip"'})
        except (OSError, ValueError): raise HTTPException(404, "Raw result not found")


class RunRequest(BaseModel):
    node_id: str
    tool_instance_id: str
    benchmark_id: str = "motor.friction.v1"
    request_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    repetitions: int = Field(default=3, ge=1, le=5, strict=True)
    max_voltage: float = Field(default=12, ge=.1, le=12, allow_inf_nan=False)
    reverse: bool = False
    inputs: dict[str, object] | None = None


class StorageRequest(BaseModel):
    keep_local: bool = False


def install_debugger_controls(app: FastAPI, service) -> None:
    results = service._debug_results
    install_result_reads(app, results)
    uploads: dict[str, dict] = {}

    async def send_command(run_id: str, command: str, metadata: dict, arguments=()):
        envelope = service._debug_envelope(debug_command_request=wire.DebugCommandRequest(
            request_id=run_id, node_id=metadata["nodeId"], tool_instance_id=metadata["toolInstanceId"],
            command_id=command, ttl_ms=1000, arguments=arguments))
        await service._server.send(envelope, service._active_peer)

    @app.post("/api/debug/runs")
    async def start_run(request: RunRequest):
        ready = service._debug_ready or {}
        definition = next((b for b in ready.get("benchmarks", []) if b["id"] == request.benchmark_id), None)
        if not definition or ready.get("nodeId") != request.node_id or ready.get("toolInstanceId") != request.tool_instance_id:
            raise HTTPException(409, "Select an available benchmark first")
        supplied = request.inputs
        if supplied is None:
            if request.benchmark_id != "motor.friction.v1":
                raise HTTPException(400, "This benchmark requires an input map")
            supplied = {"repetitions": request.repetitions, "max_voltage": request.max_voltage, "reverse": request.reverse}
        try:
            # Legacy peers/tests may omit friction input descriptors.
            if not definition.get("inputs") and request.benchmark_id == "motor.friction.v1":
                legacy = {"inputs": [
                    {"id": "repetitions", "valueType": "INT64", "minFloat64": 1, "maxFloat64": 5, "defaultValue": {"int64Value": "3"}},
                    {"id": "max_voltage", "valueType": "FLOAT64", "minFloat64": .1, "maxFloat64": 12, "defaultValue": {"float64Value": 12}},
                    {"id": "reverse", "valueType": "BOOLEAN", "defaultValue": {"booleanValue": False}},
                ]}
                arguments = encode_inputs(legacy, supplied)
            else:
                arguments = encode_inputs(definition, supplied)
        except (ValueError, TypeError) as error:
            raise HTTPException(400, str(error)) from error
        if supplied.get("reverse", False) and not definition.get("reverseAllowed", False):
            raise HTTPException(400, "Reverse is not registered")
        run_id = str(request.request_id)
        if run_id not in results.pending and ((results.path(run_id)/"manifest.json").is_file()
                or (results.root/".debugger-receipts"/f"{run_id}.json").is_file()):
            return {"runId": run_id}  # Repeated HTTP requests never replace a saved bundle.
        if any(r.get("state") in ("awaiting_acceptance", "running", "receiving", "transferring")
               and r.get("runId") != run_id for r in results.pending.values()):
            raise HTTPException(409, "Another benchmark is active")
        nodes = (service._debug_manifest or {}).get("nodes", [])
        node = next((n for n in nodes if n.get("id") == request.node_id), {})
        mechanism = next((n for n in nodes if n.get("id") == node.get("parentId")), node)
        metadata = {"nodeId": request.node_id, "toolInstanceId": request.tool_instance_id,
                    "benchmark": definition, "mechanismId": mechanism.get("id", request.node_id),
                    "mechanismLabel": mechanism.get("label", request.node_id),
                    "inputs": [MessageToDict(arg) for arg in arguments]}
        if run_id not in results.pending:
            results.expect(run_id, metadata)
        try:
            await send_command(run_id, "benchmark.run", metadata, arguments)
        except (RuntimeError, OSError) as error:
            results.pending[run_id].update(state="incomplete", message=str(error))
            raise HTTPException(409, str(error)) from error
        return {"runId": run_id}

    @app.post("/api/debug/runs/{run_id}/keepalive")
    async def keepalive(run_id: str):
        metadata = results.pending.get(run_id)
        if metadata and metadata.get("state") in ("awaiting_acceptance", "running"):
            try: await send_command(run_id, "benchmark.keepalive", metadata)
            except (RuntimeError, OSError) as error: raise HTTPException(409, str(error)) from error
        return {"ok": True}

    @app.post("/api/debug/runs/{run_id}/abort")
    async def abort(run_id: str):
        metadata = results.pending.get(run_id)
        if not metadata: raise HTTPException(409, "Run is not active")
        try: await send_command(run_id, "benchmark.abort", metadata)
        except (RuntimeError, OSError) as error: raise HTTPException(409, str(error)) from error
        return {"ok": True}

    @app.get("/api/debug/runs")
    def run_list():
        rows = results.list()
        for row in rows:
            run_id = row["runId"]
            storage = uploads.get(run_id)
            if storage is None:
                try: storage = json.loads((results.path(run_id)/"storage.json").read_text(encoding="utf-8"))
                except (OSError, ValueError): storage = {"state": "ready"}
            row["storage"] = storage
        return rows

    @app.post("/api/debug/runs/{run_id}/upload")
    async def upload(run_id: str, request: StorageRequest):
        try:
            manifest = results.manifest(run_id)
            if manifest.get("kind") != "debugger" or manifest.get("state") != "ready": raise ValueError("Result is incomplete")
        except (OSError, ValueError) as error: raise HTTPException(409, str(error)) from error
        if service._recording_uploader is None: raise HTTPException(409, "Recording upload is not configured")
        if uploads.get(run_id, {}).get("state") == "uploading": raise HTTPException(409, "Upload already active")
        uploads[run_id] = {"state": "uploading"}

        async def work():
            try:
                await asyncio.to_thread(service._recording_uploader.upload, results.path(run_id))
                uploads[run_id] = {"state": "complete", "localCopyKept": request.keep_local}
                if request.keep_local: atomic_json(results.path(run_id)/"storage.json", uploads[run_id])
                else: results.forget(run_id)
            except Exception as error:
                uploads[run_id] = {"state": "error", "detail": str(error)}
        asyncio.create_task(work(), name=f"debugger-upload-{run_id}")
        return uploads[run_id]

    @app.delete("/api/debug/runs/{run_id}")
    def discard(run_id: str):
        if uploads.get(run_id, {}).get("state") == "uploading": raise HTTPException(409, "Upload is active")
        try:
            if results.manifest(run_id).get("state") != "ready": raise ValueError("Result is incomplete")
            results.forget(run_id)
        except (OSError, ValueError) as error: raise HTTPException(409, str(error)) from error
        return {"discarded": True}
