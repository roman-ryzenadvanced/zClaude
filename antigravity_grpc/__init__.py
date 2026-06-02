"""
antigravity_grpc — gRPC fallback client for Google CloudCode (Antigravity).

When the REST API rejects a request (404 model not found, 400 bad request due to
model ID mismatch, etc.), this module provides a gRPC fallback path that uses
Google's native PredictionService protocol — the same one the agy CLI uses.

This module is imported lazily and only when grpcio is installed. If grpcio is
not available, the fallback is silently skipped.
"""

from .client import (
    GrpcFallbackResult,
    AntigravityGrpcClient,
    is_grpc_available,
    get_client,
)

__all__ = [
    "GrpcFallbackResult",
    "AntigravityGrpcClient",
    "is_grpc_available",
    "get_client",
]
