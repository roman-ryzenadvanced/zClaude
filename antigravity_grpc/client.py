"""
antigravity_grpc.client — gRPC fallback client for Google CloudCode (Antigravity).

This module provides a gRPC client that can be used as an automatic fallback when
the CloudCode REST API rejects requests. The gRPC path uses the same
PredictionService that the native agy CLI binary uses, giving access to models
that are unavailable via REST (e.g. models that return 404 on REST but work on gRPC).

Key design decisions:
  - Lazy import: grpcio is only imported when actually needed. If not installed,
    is_grpc_available() returns False and the fallback is silently skipped.
  - Zero impact on other providers: this module is only called from
    _handle_antigravity_v2() when REST returns a fallback-eligible error.
  - Same output format as REST: the client returns structured dicts that match
    the SSE/JSON response shapes the proxy already processes.
  - Thread-safe: the gRPC channel is created once per endpoint and reused.

Usage from translate-proxy.py:
    from antigravity_grpc import is_grpc_available, AntigravityGrpcClient

    if is_grpc_available():
        client = AntigravityGrpcClient()
        result = client.try_generate(request_dict, stream=False)
        if result.ok:
            # Use result.response_data (dict matching REST response shape)
        else:
            # gRPC also failed, fall through to error
"""

import json
import os
import sys
import time
import threading
import collections

# ═══════════════════════════════════════════════════════════════════
# Lazy gRPC import — never crash if grpcio is missing
# ═══════════════════════════════════════════════════════════════════

_grpc = None
_pb2 = None
_pb2_grpc = None
_import_error = None

def _try_import():
    global _grpc, _pb2, _pb2_grpc, _import_error
    if _grpc is not None:
        return _grpc is not False
    try:
        import grpc as _real_grpc
        # Import the generated stubs relative to this package
        from . import cloudcode_pb2 as _real_pb2
        from . import cloudcode_pb2_grpc as _real_pb2_grpc
        _grpc = _real_grpc
        _pb2 = _real_pb2
        _pb2_grpc = _real_pb2_grpc
        return True
    except Exception as e:
        _import_error = str(e)
        _grpc = False
        return False


def is_grpc_available():
    """Return True if grpcio and the generated stubs are importable."""
    return _try_import()


# ═══════════════════════════════════════════════════════════════════
# gRPC endpoints for Antigravity (same hosts, different port/path)
# ═══════════════════════════════════════════════════════════════════
# The CloudCode gRPC service runs on the same hosts as REST but uses
# the gRPC protocol. The agy CLI connects to:
#   - cloudcode-pa.googleapis.com:443
#   - daily-cloudcode-pa.googleapis.com:443
#   - daily-cloudcode-pa.sandbox.googleapis.com:443

_GRPC_ENDPOINTS = [
    "daily-cloudcode-pa.googleapis.com:443",
    "cloudcode-pa.googleapis.com:443",
]

_ALLOW_STAGING_ENV = "ALLOW_ANTIGRAVITY_STAGING"

# ═══════════════════════════════════════════════════════════════════
# Result type
# ═══════════════════════════════════════════════════════════════════

class GrpcFallbackResult:
    """Result of a gRPC fallback attempt."""

    __slots__ = ("ok", "response_data", "stream_chunks", "error_message",
                 "endpoint_used", "model_used", "elapsed_s")

    def __init__(self, ok=False, response_data=None, stream_chunks=None,
                 error_message="", endpoint_used="", model_used="", elapsed_s=0.0):
        self.ok = ok
        self.response_data = response_data      # dict (non-streaming)
        self.stream_chunks = stream_chunks      # list[dict] (streaming)
        self.error_message = error_message
        self.endpoint_used = endpoint_used
        self.model_used = model_used
        self.elapsed_s = elapsed_s

    def __repr__(self):
        if self.ok:
            if self.stream_chunks is not None:
                return f"<GrpcFallbackResult OK stream chunks={len(self.stream_chunks)}>"
            return f"<GrpcFallbackResult OK data_keys={list(self.response_data.keys()) if self.response_data else None}>"
        return f"<GrpcFallbackResult FAIL error={self.error_message!r}>"


# ═══════════════════════════════════════════════════════════════════
# JSON → Protobuf conversion helpers
# ═══════════════════════════════════════════════════════════════════

def _struct_to_protobuf(d, struct_obj=None):
    """Convert a Python dict to a google.protobuf.Struct."""
    from google.protobuf.struct_pb2 import Struct, Value, NullValue, ListValue
    if struct_obj is None:
        struct_obj = Struct()
    if isinstance(d, dict):
        for k, v in d.items():
            if isinstance(v, str):
                struct_obj.fields[k].string_value = v
            elif isinstance(v, bool):
                struct_obj.fields[k].bool_value = v
            elif isinstance(v, int):
                struct_obj.fields[k].number_value = float(v)
            elif isinstance(v, float):
                struct_obj.fields[k].number_value = v
            elif isinstance(v, dict):
                _struct_to_protobuf(v, struct_obj.fields[k].struct_value)
            elif isinstance(v, list):
                lst = struct_obj.fields[k].list_value
                for item in v:
                    if isinstance(item, str):
                        lst.values.add().string_value = item
                    elif isinstance(item, bool):
                        lst.values.add().bool_value = item
                    elif isinstance(item, (int, float)):
                        lst.values.add().number_value = float(item)
                    elif isinstance(item, dict):
                        _struct_to_protobuf(item, lst.values.add().struct_value)
                    elif item is None:
                        lst.values.add().null_value = 0
            elif v is None:
                struct_obj.fields[k].null_value = 0
    return struct_obj


def _protobuf_struct_to_dict(struct):
    """Convert a google.protobuf.Struct to a Python dict."""
    from google.protobuf.struct_pb2 import Value, NullValue
    result = {}
    for k, v in struct.fields.items():
        kind = v.WhichOneof("kind")
        if kind == "null_value":
            result[k] = None
        elif kind == "number_value":
            result[k] = v.number_value
        elif kind == "string_value":
            result[k] = v.string_value
        elif kind == "bool_value":
            result[k] = v.bool_value
        elif kind == "struct_value":
            result[k] = _protobuf_struct_to_dict(v.struct_value)
        elif kind == "list_value":
            result[k] = [_value_to_python(item) for item in v.list_value.values]
        else:
            result[k] = None
    return result


def _value_to_python(v):
    """Convert a google.protobuf.Value to a Python value."""
    kind = v.WhichOneof("kind")
    if kind == "null_value":
        return None
    elif kind == "number_value":
        return v.number_value
    elif kind == "string_value":
        return v.string_value
    elif kind == "bool_value":
        return v.bool_value
    elif kind == "struct_value":
        return _protobuf_struct_to_dict(v.struct_value)
    elif kind == "list_value":
        return [_value_to_python(item) for item in v.list_value.values]
    return None


def _json_parts_to_proto(parts_json):
    """Convert a list of JSON content parts to protobuf Part messages."""
    result = []
    for p in parts_json:
        if not isinstance(p, dict):
            continue
        part = _pb2.Part()

        # Thought signature
        sig = p.get("thoughtSignature") or p.get("thought_signature")
        if sig:
            part.thought_signature = sig

        if p.get("thought"):
            part.thought = True
            if "text" in p:
                part.text = p["text"]
        elif "text" in p and "functionCall" not in p:
            part.text = p["text"]
        elif "functionCall" in p:
            fc = p["functionCall"]
            part.function_call.name = fc.get("name", "")
            part.function_call.id = fc.get("id", "")
            args = fc.get("args", fc.get("arguments", {}))
            if isinstance(args, dict):
                _struct_to_protobuf(args, part.function_call.args)
            elif isinstance(args, str):
                try:
                    _struct_to_protobuf(json.loads(args), part.function_call.args)
                except Exception:
                    pass
        elif "functionResponse" in p:
            fr = p["functionResponse"]
            part.function_response.name = fr.get("name", "")
            part.function_response.id = fr.get("id", "")
            resp = fr.get("response", {})
            if "result" in resp:
                result_val = resp["result"]
                if isinstance(result_val, (dict, list)):
                    _struct_to_protobuf({"result": result_val}, part.function_response.response)
                else:
                    _struct_to_protobuf({"result": str(result_val)}, part.function_response.response)
            elif isinstance(resp, dict):
                _struct_to_protobuf(resp, part.function_response.response)
        elif "inlineData" in p:
            idata = p["inlineData"]
            import base64
            part.inline_data.mime_type = idata.get("mimeType", "image/png")
            b64data = idata.get("data", "")
            part.inline_data.data = base64.b64decode(b64data) if b64data else b""

        result.append(part)
    return result


def _json_contents_to_proto(contents_json):
    """Convert a list of JSON content objects to protobuf Content messages."""
    result = []
    for c in contents_json:
        if not isinstance(c, dict):
            continue
        content = _pb2.Content()
        content.role = c.get("role", "user")
        for part in _json_parts_to_proto(c.get("parts", [])):
            content.parts.append(part)
        result.append(content)
    return result


def _proto_candidate_to_json(candidate):
    """Convert a protobuf Candidate to a JSON-compatible dict."""
    content_json = {"role": candidate.content.role, "parts": []}
    for part in candidate.content.parts:
        p = {}
        if part.thought_signature:
            p["thoughtSignature"] = part.thought_signature
        if part.thought:
            p["thought"] = True
            if part.text:
                p["text"] = part.text
        elif part.text and not part.HasField("function_call"):
            p["text"] = part.text
        elif part.HasField("function_call"):
            fc = part.function_call
            args_dict = _protobuf_struct_to_dict(fc.args) if fc.HasField("args") else {}
            p["functionCall"] = {
                "name": fc.name,
                "args": args_dict,
                "id": fc.id,
            }
        elif part.HasField("function_response"):
            fr = part.function_response
            resp_dict = _protobuf_struct_to_dict(fr.response) if fr.HasField("response") else {}
            p["functionResponse"] = {
                "name": fr.name,
                "response": resp_dict,
                "id": fr.id,
            }
        elif part.HasField("inline_data"):
            import base64
            p["inlineData"] = {
                "mimeType": part.inline_data.mime_type,
                "data": base64.b64encode(part.inline_data.data).decode(),
            }
        if p:
            content_json["parts"].append(p)

    return {
        "content": content_json,
        "finishReason": candidate.finish_reason,
        "index": candidate.index,
    }


# ═══════════════════════════════════════════════════════════════════
# Client
# ═══════════════════════════════════════════════════════════════════

class AntigravityGrpcClient:
    """
    gRPC fallback client for Google CloudCode Antigravity.

    Thread-safe. Channels are cached per endpoint and reused.
    """

    def __init__(self):
        self._channels = {}
        self._stubs = {}
        self._lock = threading.Lock()

    def _get_channel(self, endpoint):
        """Get or create a gRPC channel for the given endpoint."""
        with self._lock:
            if endpoint not in self._channels:
                # Use secure channel with default SSL credentials
                creds = _grpc.ssl_channel_credentials()
                channel = _grpc.secure_channel(endpoint, creds)
                self._channels[endpoint] = channel
                self._stubs[endpoint] = _pb2_grpc.PredictionServiceStub(channel)
            return self._channels[endpoint], self._stubs[endpoint]

    def _build_request(self, wrapped_dict):
        """
        Build a GenerateContentRequest protobuf from the same wrapped dict
        that the REST API uses.

        wrapped_dict shape:
        {
            "project": "...",
            "model": "...",
            "requestType": "agent",
            "userAgent": "antigravity/...",
            "requestId": "agent-...",
            "request": {
                "contents": [...],
                "systemInstruction": {...},
                "generationConfig": {...},
                "tools": [...],
                "safetySettings": [...],
                "toolConfig": {...},
                "sessionId": "..."
            }
        }
        """
        req = _pb2.GenerateContentRequest()
        req.project = wrapped_dict.get("project", "")
        req.model = wrapped_dict.get("model", "")
        req.request_type = wrapped_dict.get("requestType", "agent")
        req.user_agent = wrapped_dict.get("userAgent", "")
        req.request_id = wrapped_dict.get("requestId", "")

        inner = wrapped_dict.get("request", {})

        # Contents
        for c in _json_contents_to_proto(inner.get("contents", [])):
            req.request.contents.append(c)

        # System instruction
        si = inner.get("systemInstruction", {})
        if si:
            si_parts = si.get("parts", [])
            if si.get("role"):
                req.request.system_instruction.role = si.get("role", "user")
            for part in _json_parts_to_proto(si_parts):
                req.request.system_instruction.parts.append(part)

        # Generation config
        gc = inner.get("generationConfig", {})
        if gc:
            cfg = req.request.generation_config
            if "maxOutputTokens" in gc:
                cfg.max_output_tokens = int(gc["maxOutputTokens"])
            if "temperature" in gc:
                cfg.temperature = float(gc["temperature"])
            if "topP" in gc:
                cfg.top_p = float(gc["top_p" if "top_p" in gc else "topP"])
            for ss in gc.get("stopSequences", []):
                cfg.stop_sequences.append(ss)

            # Thinking config (Gemini 3 native)
            tc = gc.get("thinkingConfig", gc.get("thinking_config"))
            if tc:
                cfg.thinking_config.include_thoughts = tc.get("includeThoughts", tc.get("include_thoughts", False))
                cfg.thinking_config.thinking_budget = int(tc.get("thinkingBudget", tc.get("thinking_budget", 8192)))
            # Legacy thinking fields
            if "includeThoughts" in gc and not tc:
                cfg.thinking_config.include_thoughts = gc["includeThoughts"]
            if "thinkingBudget" in gc and not tc:
                cfg.thinking_config.thinking_budget = int(gc["thinkingBudget"])

        # Tools
        for tool_json in inner.get("tools", []):
            tool = _pb2.Tool()
            for fd_json in tool_json.get("functionDeclarations", []):
                fd = tool.function_declarations.add()
                fd.name = fd_json.get("name", "")
                fd.description = fd_json.get("description", "")
                params = fd_json.get("parameters", {})
                if isinstance(params, dict) and params:
                    _struct_to_protobuf(params, fd.parameters)
            req.request.tools.append(tool)

        # Safety settings
        for ss in inner.get("safetySettings", []):
            ss_msg = _pb2.SafetySetting()
            ss_msg.category = ss.get("category", "")
            ss_msg.threshold = ss.get("threshold", "OFF")
            req.request.safety_settings.append(ss_msg)

        # Tool config
        tcfg = inner.get("toolConfig", {})
        if tcfg:
            fcc = tcfg.get("functionCallingConfig", {})
            if fcc:
                req.request.tool_config.function_calling_config.mode = fcc.get("mode", "AUTO")
                for afn in fcc.get("allowed_function_names", []):
                    req.request.tool_config.function_calling_config.allowed_function_names.append(afn)

        # Session ID
        sid = inner.get("sessionId", "")
        if sid:
            req.request.session_id = sid

        return req

    def try_generate(self, wrapped_dict, stream=False, access_token="",
                     timeout_s=180):
        """
        Try a gRPC GenerateContent or StreamGenerateContent request.

        Args:
            wrapped_dict: The same wrapped dict used for REST requests.
            stream: If True, use server-streaming RPC.
            access_token: OAuth2 Bearer token for authentication.
            timeout_s: Request timeout in seconds.

        Returns:
            GrpcFallbackResult with ok=True if successful.
            For non-streaming: result.response_data is a dict matching
                the REST JSON response shape.
            For streaming: result.stream_chunks is a list of dicts matching
                REST SSE chunk shapes.
        """
        if not is_grpc_available():
            return GrpcFallbackResult(ok=False, error_message="grpcio not installed")

        t0 = time.time()

        # Build metadata (gRPC uses metadata instead of HTTP headers)
        metadata = []
        if access_token:
            metadata.append(("authorization", f"Bearer {access_token}"))
        ua = wrapped_dict.get("userAgent", "")
        if ua:
            metadata.append(("user-agent", ua))
        metadata.append(("x-client-name", "antigravity"))
        # Required for Google's gRPC gateway
        metadata.append(("x-goog-api-client", "gl-node/18.18.2 fire/0.8.6 grpc/1.10.x"))

        # Build endpoints list
        endpoints = list(_GRPC_ENDPOINTS)
        if os.environ.get(_ALLOW_STAGING_ENV, "0") == "1":
            endpoints.append("daily-cloudcode-pa.sandbox.googleapis.com:443")
            endpoints.append("autopush-cloudcode-pa.sandbox.googleapis.com:443")

        model = wrapped_dict.get("model", "?")

        last_error = ""
        for ep in endpoints:
            try:
                channel, stub = self._get_channel(ep)
                req = self._build_request(wrapped_dict)

                if stream:
                    return self._do_stream(stub, req, metadata, ep, model,
                                           timeout_s, t0)
                else:
                    return self._do_unary(stub, req, metadata, ep, model,
                                          timeout_s, t0)

            except Exception as e:
                last_error = str(e)
                err_str = last_error.lower()
                print(f"[antigravity-grpc] {ep} failed: {last_error[:300]}", file=sys.stderr)
                # Don't retry on auth errors
                if "unauthenticated" in err_str or "permission" in err_str:
                    break
                # Don't retry on invalid argument (model truly doesn't exist)
                if "not_found" in err_str or "not found" in err_str:
                    break
                continue

        elapsed = time.time() - t0
        return GrpcFallbackResult(
            ok=False,
            error_message=f"All gRPC endpoints failed: {last_error}",
            model_used=model,
            elapsed_s=elapsed,
        )

    def _do_unary(self, stub, req, metadata, endpoint, model, timeout_s, t0):
        """Execute a unary (non-streaming) gRPC call."""
        response = stub.GenerateContent(
            req,
            metadata=metadata,
            timeout=timeout_s,
        )
        elapsed = time.time() - t0

        # Convert protobuf response to REST-compatible JSON shape
        candidates_json = []
        for candidate in response.response.candidates:
            candidates_json.append(_proto_candidate_to_json(candidate))

        # Match the REST response envelope:
        # { "response": { "candidates": [...] } }
        rest_shape = {
            "response": {
                "candidates": candidates_json,
            }
        }

        print(f"[antigravity-grpc] {endpoint} unary OK, candidates={len(candidates_json)}, elapsed={elapsed:.1f}s", file=sys.stderr)

        return GrpcFallbackResult(
            ok=True,
            response_data=rest_shape,
            endpoint_used=endpoint,
            model_used=model,
            elapsed_s=elapsed,
        )

    def _do_stream(self, stub, req, metadata, endpoint, model, timeout_s, t0):
        """Execute a server-streaming gRPC call."""
        chunks = []
        chunk_count = 0

        response_iter = stub.StreamGenerateContent(
            req,
            metadata=metadata,
            timeout=timeout_s,
        )

        for chunk_proto in response_iter:
            chunk_count += 1
            # Each chunk_proto is a StreamGenerateContentChunk
            # which wraps a Response with candidates
            candidates_json = []
            for candidate in chunk_proto.response.candidates:
                candidates_json.append(_proto_candidate_to_json(candidate))

            # Match REST SSE chunk shape: { "response": { "candidates": [...] } }
            chunk_json = {
                "response": {
                    "candidates": candidates_json,
                }
            }
            chunks.append(chunk_json)

        elapsed = time.time() - t0
        print(f"[antigravity-grpc] {endpoint} stream OK, chunks={chunk_count}, elapsed={elapsed:.1f}s", file=sys.stderr)

        return GrpcFallbackResult(
            ok=True,
            stream_chunks=chunks,
            endpoint_used=endpoint,
            model_used=model,
            elapsed_s=elapsed,
        )

    def close(self):
        """Close all gRPC channels."""
        with self._lock:
            for ep, channel in self._channels.items():
                try:
                    channel.close()
                except Exception:
                    pass
            self._channels.clear()
            self._stubs.clear()


# ═══════════════════════════════════════════════════════════════════
# Module-level singleton
# ═══════════════════════════════════════════════════════════════════

_client = None
_client_lock = threading.Lock()

def get_client():
    """Get the module-level AntigravityGrpcClient singleton."""
    global _client
    with _client_lock:
        if _client is None:
            _client = AntigravityGrpcClient()
        return _client
