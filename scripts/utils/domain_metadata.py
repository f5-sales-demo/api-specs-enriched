# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Generate and manage domain metadata for CLI tool integration.

This utility provides programmatic assignment of metadata fields to domains
based on their characteristics and category, ensuring idempotent generation
suitable for CICD automation.
"""

from pathlib import Path
from typing import Any

import yaml

from scripts.utils.resource_resolver import (
    apply_overrides,
    resolve_resource,
    validate_resource_mappings,
)

# Resource metadata cache for per-resource metadata from config/resource_metadata.yaml
# Uses mutable container pattern to avoid global statement (PLW0603)
_CACHE: dict[str, Any] = {}

# =============================================================================
# SVG Icon Library - URL-encoded data URIs for embedded icons
# Each icon is ~200-400 bytes, works in <img src>, CSS url(), React/Vue
# =============================================================================

SVG_ICONS = {
    # Infrastructure icons
    "antenna": "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='%236366F1'%3E%3Cpath d='M12 5c-3.87 0-7 3.13-7 7h2c0-2.76 2.24-5 5-5s5 2.24 5 5h2c0-3.87-3.13-7-7-7zm0-4C5.93 1 1 5.93 1 12h2c0-4.97 4.03-9 9-9s9 4.03 9 9h2c0-6.07-4.93-11-11-11zm0 8c-1.66 0-3 1.34-3 3s1.34 3 3 3 3-1.34 3-3-1.34-3-3-3z'/%3E%3C/svg%3E",
    "cloud": "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='%2306B6D4'%3E%3Cpath d='M19.35 10.04C18.67 6.59 15.64 4 12 4 9.11 4 6.6 5.64 5.35 8.04 2.34 8.36 0 10.91 0 14c0 3.31 2.69 6 6 6h13c2.76 0 5-2.24 5-5 0-2.64-2.05-4.78-4.65-4.96z'/%3E%3C/svg%3E",
    "box": "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='%238B5CF6'%3E%3Cpath d='M21 16.5c0 .38-.21.71-.53.88l-7.9 4.44c-.16.12-.36.18-.57.18s-.41-.06-.57-.18l-7.9-4.44A.991.991 0 0 1 3 16.5v-9c0-.38.21-.71.53-.88l7.9-4.44c.16-.12.36-.18.57-.18s.41.06.57.18l7.9 4.44c.32.17.53.5.53.88v9zM12 4.15L5 8.09v7.82l7 3.94 7-3.94V8.09l-7-3.94z'/%3E%3C/svg%3E",
    "gear": "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='%2364748B'%3E%3Cpath d='M19.14 12.94c.04-.31.06-.63.06-.94 0-.31-.02-.63-.06-.94l2.03-1.58a.49.49 0 0 0 .12-.61l-1.92-3.32a.488.488 0 0 0-.59-.22l-2.39.96c-.5-.38-1.03-.7-1.62-.94l-.36-2.54a.484.484 0 0 0-.48-.41h-3.84c-.24 0-.43.17-.47.41l-.36 2.54c-.59.24-1.13.57-1.62.94l-2.39-.96c-.22-.08-.47 0-.59.22L2.74 8.87c-.12.21-.08.47.12.61l2.03 1.58c-.04.31-.06.63-.06.94s.02.63.06.94l-2.03 1.58a.49.49 0 0 0-.12.61l1.92 3.32c.12.22.37.29.59.22l2.39-.96c.5.38 1.03.7 1.62.94l.36 2.54c.05.24.24.41.48.41h3.84c.24 0 .44-.17.47-.41l.36-2.54c.59-.24 1.13-.56 1.62-.94l2.39.96c.22.08.47 0 .59-.22l1.92-3.32c.12-.22.07-.47-.12-.61l-2.01-1.58zM12 15.6c-1.98 0-3.6-1.62-3.6-3.6s1.62-3.6 3.6-3.6 3.6 1.62 3.6 3.6-1.62 3.6-3.6 3.6z'/%3E%3C/svg%3E",
    "web": "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='%23A855F7'%3E%3Cpath d='M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 17.93c-3.95-.49-7-3.85-7-7.93 0-.62.08-1.21.21-1.79L9 15v1c0 1.1.9 2 2 2v1.93zm6.9-2.54c-.26-.81-1-1.39-1.9-1.39h-1v-3c0-.55-.45-1-1-1H8v-2h2c.55 0 1-.45 1-1V7h2c1.1 0 2-.9 2-2v-.41c2.93 1.19 5 4.06 5 7.41 0 2.08-.8 3.97-2.1 5.39z'/%3E%3C/svg%3E",
    "globe": "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='%2310B981'%3E%3Cpath d='M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 17.93c-3.95-.49-7-3.85-7-7.93 0-.62.08-1.21.21-1.79L9 15v1c0 1.1.9 2 2 2v1.93zm6.9-2.54c-.26-.81-1-1.39-1.9-1.39h-1v-3c0-.55-.45-1-1-1H8v-2h2c.55 0 1-.45 1-1V7h2c1.1 0 2-.9 2-2v-.41c2.93 1.19 5 4.06 5 7.41 0 2.08-.8 3.97-2.1 5.39z'/%3E%3C/svg%3E",
    "wrench": "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='%23F59E0B'%3E%3Cpath d='M22.7 19l-9.1-9.1c.9-2.3.4-5-1.5-6.9-2-2-5-2.4-7.4-1.3L9 6 6 9 1.6 4.7C.4 7.1.9 10.1 2.9 12.1c1.9 1.9 4.6 2.4 6.9 1.5l9.1 9.1c.4.4 1 .4 1.4 0l2.3-2.3c.5-.4.5-1.1.1-1.4z'/%3E%3C/svg%3E",
    "server": "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='%2364748B'%3E%3Cpath d='M20 13H4c-.55 0-1 .45-1 1v6c0 .55.45 1 1 1h16c.55 0 1-.45 1-1v-6c0-.55-.45-1-1-1zM7 19c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2zM20 3H4c-.55 0-1 .45-1 1v6c0 .55.45 1 1 1h16c.55 0 1-.45 1-1V4c0-.55-.45-1-1-1zM7 9c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2z'/%3E%3C/svg%3E",
    "building": "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='%23EF4444'%3E%3Cpath d='M12 7V3H2v18h20V7H12zM6 19H4v-2h2v2zm0-4H4v-2h2v2zm0-4H4V9h2v2zm0-4H4V5h2v2zm4 12H8v-2h2v2zm0-4H8v-2h2v2zm0-4H8V9h2v2zm0-4H8V5h2v2zm10 12h-8v-2h2v-2h-2v-2h2v-2h-2V9h8v10zm-2-8h-2v2h2v-2zm0 4h-2v2h2v-2z'/%3E%3C/svg%3E",
    "circle": "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='%2322C55E'%3E%3Ccircle cx='12' cy='12' r='10'/%3E%3C/svg%3E",
    "cabinet": "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='%2378716C'%3E%3Cpath d='M20 2H4c-1.1 0-2 .9-2 2v16c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zM8 20H4v-8h4v8zm6 0h-4v-8h4v8zm6 0h-4v-8h4v8zm0-10H4V4h16v6z'/%3E%3C/svg%3E",
    # Networking icons
    "globe_network": "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='%232563EB'%3E%3Cpath d='M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 17.93c-3.95-.49-7-3.85-7-7.93 0-.62.08-1.21.21-1.79L9 15v1c0 1.1.9 2 2 2v1.93zm6.9-2.54c-.26-.81-1-1.39-1.9-1.39h-1v-3c0-.55-.45-1-1-1H8v-2h2c.55 0 1-.45 1-1V7h2c1.1 0 2-.9 2-2v-.41c2.93 1.19 5 4.06 5 7.41 0 2.08-.8 3.97-2.1 5.39z'/%3E%3C/svg%3E",
    "balance": "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='%234F46E5'%3E%3Cpath d='M12 3c-1.27 0-2.4.8-2.82 2H3v2h1.95L2 14c-.47 2 1 4 4 4s4.47-2 4-4L7.05 7H9.1c.42 1.2 1.55 2 2.9 2s2.4-.8 2.82-2h2.13L14 14c-.47 2 1 4 4 4s4.47-2 4-4l-2.95-7H21V5h-6.18c-.42-1.2-1.55-2-2.82-2zm-6 12.5c-.73 0-1.45-.3-1.97-.82L6 10l1.97 4.68c-.52.52-1.24.82-1.97.82zm12 0c-.73 0-1.45-.3-1.97-.82L18 10l1.97 4.68c-.52.52-1.24.82-1.97.82z'/%3E%3C/svg%3E",
    "rocket": "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='%23F97316'%3E%3Cpath d='M12 2.5s4.5 2.04 4.5 10c0 3.22-1.67 5.6-3.25 7.08L12 22l-1.25-2.42C9.17 18.1 7.5 15.72 7.5 12.5c0-7.96 4.5-10 4.5-10zm0 8c1.1 0 2-.9 2-2s-.9-2-2-2-2 .9-2 2 .9 2 2 2zM5 14.5c0 1.22.57 2.36 1.44 3.22l1.76-1.76c-.43-.43-.7-1.01-.7-1.66 0-.25.04-.49.1-.72L5.21 12.1c-.13.77-.21 1.58-.21 2.4zm14 0c0-.82-.08-1.63-.21-2.4l-2.39 1.48c.06.23.1.47.1.72 0 .65-.27 1.23-.7 1.66l1.76 1.76c.87-.86 1.44-2 1.44-3.22z'/%3E%3C/svg%3E",
    "plug": "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='%233B82F6'%3E%3Cpath d='M16 9v4.66l-3.5 3.51V19h-1v-1.83L8 13.65V9h8m0-6h-2v4h-4V3H8v4H6v6.5l3.5 3.5v5h5v-5l3.5-3.5V7h-2V3z'/%3E%3C/svg%3E",
    # Security icons
    "shield": "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='%2310B981'%3E%3Cpath d='M12 1L3 5v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5l-9-4z'/%3E%3C/svg%3E",
    "robot": "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='%238B5CF6'%3E%3Cpath d='M22 14h-1c0-3.87-3.13-7-7-7h-1V5.73c.6-.34 1-.99 1-1.73 0-1.1-.9-2-2-2s-2 .9-2 2c0 .74.4 1.39 1 1.73V7h-1c-3.87 0-7 3.13-7 7H2c-.55 0-1 .45-1 1v3c0 .55.45 1 1 1h1v1c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2v-1h1c.55 0 1-.45 1-1v-3c0-.55-.45-1-1-1zM8.5 18c-.83 0-1.5-.67-1.5-1.5S7.67 15 8.5 15s1.5.67 1.5 1.5S9.33 18 8.5 18zm3.5-5H8v-2h4v2zm4 5c-.83 0-1.5-.67-1.5-1.5s.67-1.5 1.5-1.5 1.5.67 1.5 1.5-.67 1.5-1.5 1.5z'/%3E%3C/svg%3E",
    "lock_key": "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='%23EF4444'%3E%3Cpath d='M12 1L3 5v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5l-9-4zm0 10.99h7c-.53 4.12-3.28 7.79-7 8.94V12H5V6.3l7-3.11v8.8z'/%3E%3C/svg%3E",
    "lock": "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='%23F59E0B'%3E%3Cpath d='M18 8h-1V6c0-2.76-2.24-5-5-5S7 3.24 7 6v2H6c-1.1 0-2 .9-2 2v10c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V10c0-1.1-.9-2-2-2zm-6 9c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2zm3.1-9H8.9V6c0-1.71 1.39-3.1 3.1-3.1 1.71 0 3.1 1.39 3.1 3.1v2z'/%3E%3C/svg%3E",
    "scroll": "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='%2314B8A6'%3E%3Cpath d='M14 2H6c-1.1 0-1.99.9-1.99 2L4 20c0 1.1.89 2 1.99 2H18c1.1 0 2-.9 2-2V8l-6-6zm2 16H8v-2h8v2zm0-4H8v-2h8v2zm-3-5V3.5L18.5 9H13z'/%3E%3C/svg%3E",
    "sealed": "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='%236366F1'%3E%3Cpath d='M18 8h-1V6c0-2.76-2.24-5-5-5S7 3.24 7 6h2c0-1.66 1.34-3 3-3s3 1.34 3 3v2H6c-1.1 0-2 .9-2 2v10c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V10c0-1.1-.9-2-2-2zm0 12H6V10h12v10zm-6-3c1.1 0 2-.9 2-2s-.9-2-2-2-2 .9-2 2 .9 2 2 2z'/%3E%3C/svg%3E",
    "stop": "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='%23DC2626'%3E%3Cpath d='M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm5 11H7v-2h10v2z'/%3E%3C/svg%3E",
    "timer": "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='%23F97316'%3E%3Cpath d='M15 1H9v2h6V1zm-4 13h2V8h-2v6zm8.03-6.61 1.42-1.42c-.43-.51-.9-.99-1.41-1.41l-1.42 1.42C16.07 4.74 14.12 4 12 4c-4.97 0-9 4.03-9 9s4.02 9 9 9 9-4.03 9-9c0-2.12-.74-4.07-1.97-5.61zM12 20c-3.87 0-7-3.13-7-7s3.13-7 7-7 7 3.13 7 7-3.13 7-7 7z'/%3E%3C/svg%3E",
    "mask": "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='%23A855F7'%3E%3Cpath d='M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 18c-4.41 0-8-3.59-8-8s3.59-8 8-8 8 3.59 8 8-3.59 8-8 8zm-5-9c.83 0 1.5-.67 1.5-1.5S7.83 8 7 8s-1.5.67-1.5 1.5S6.17 11 7 11zm10 0c.83 0 1.5-.67 1.5-1.5S17.83 8 17 8s-1.5.67-1.5 1.5.67 1.5 1.5 1.5zM12 17.5c2.33 0 4.31-1.46 5.11-3.5H6.89c.8 2.04 2.78 3.5 5.11 3.5z'/%3E%3C/svg%3E",
    "warning": "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='%23FBBF24'%3E%3Cpath d='M1 21h22L12 2 1 21zm12-3h-2v-2h2v2zm0-4h-2v-4h2v4z'/%3E%3C/svg%3E",
    "virus": "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='%23EF4444'%3E%3Cpath d='M19.5 5.5 18 4l-1.5 1.5L18 7l1.5-1.5zM12 2v3m0 14v3m10-10h-3M5 12H2m15.5 6.5L18 20l1.5-1.5L18 17l-1.5 1.5zm-11 0L6 20l-1.5-1.5L6 17l-1.5 1.5zm0-11L6 4l-1.5 1.5L6 7 4.5 5.5zM12 7c-2.76 0-5 2.24-5 5s2.24 5 5 5 5-2.24 5-5-2.24-5-5-5zm0 8c-1.66 0-3-1.34-3-3s1.34-3 3-3 3 1.34 3 3-1.34 3-3 3z'/%3E%3C/svg%3E",
    "siren": "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='%23DC2626'%3E%3Cpath d='M12 2L4 5v6.09c0 5.05 3.41 9.76 8 10.91 4.59-1.15 8-5.86 8-10.91V5l-8-3zm6 9.09c0 4-2.55 7.7-6 8.83-3.45-1.13-6-4.82-6-8.83V6.31l6-2.25 6 2.25v4.78zM11 7h2v6h-2zm0 8h2v2h-2z'/%3E%3C/svg%3E",
    "monitor": "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='%233B82F6'%3E%3Cpath d='M21 2H3c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h7v2H8v2h8v-2h-2v-2h7c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zm0 14H3V4h18v12z'/%3E%3C/svg%3E",
    # Platform icons
    "key": "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='%23FBBF24'%3E%3Cpath d='M12.65 10C11.83 7.67 9.61 6 7 6c-3.31 0-6 2.69-6 6s2.69 6 6 6c2.61 0 4.83-1.67 5.65-4H17v4h4v-4h2v-4H12.65zM7 14c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2z'/%3E%3C/svg%3E",
    "people": "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='%236366F1'%3E%3Cpath d='M16 11c1.66 0 2.99-1.34 2.99-3S17.66 5 16 5c-1.66 0-3 1.34-3 3s1.34 3 3 3zm-8 0c1.66 0 2.99-1.34 2.99-3S9.66 5 8 5C6.34 5 5 6.34 5 8s1.34 3 3 3zm0 2c-2.33 0-7 1.17-7 3.5V19h14v-2.5c0-2.33-4.67-3.5-7-3.5zm8 0c-.29 0-.62.02-.97.05 1.16.84 1.97 1.97 1.97 3.45V19h6v-2.5c0-2.33-4.67-3.5-7-3.5z'/%3E%3C/svg%3E",
    "ticket": "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='%2314B8A6'%3E%3Cpath d='M22 10V6c0-1.11-.9-2-2-2H4c-1.1 0-1.99.89-1.99 2v4c1.1 0 1.99.9 1.99 2s-.89 2-2 2v4c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2v-4c-1.1 0-2-.9-2-2s.9-2 2-2zm-2-1.46c-1.19.69-2 1.99-2 3.46s.81 2.77 2 3.46V18H4v-2.54c1.19-.69 2-1.99 2-3.46 0-1.48-.8-2.77-1.99-3.46L4 6h16v2.54z'/%3E%3C/svg%3E",
    "store": "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='%23F97316'%3E%3Cpath d='M18.36 9l.6 3H5.04l.6-3h12.72M20 4H4v2h16V4zm0 3H4l-1 5v2h1v6h10v-6h4v6h2v-6h1v-2l-1-5zM6 18v-4h6v4H6z'/%3E%3C/svg%3E",
    "card": "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='%2310B981'%3E%3Cpath d='M20 4H4c-1.11 0-1.99.89-1.99 2L2 18c0 1.11.89 2 2 2h16c1.11 0 2-.89 2-2V6c0-1.11-.89-2-2-2zm0 14H4v-6h16v6zm0-10H4V6h16v2z'/%3E%3C/svg%3E",
    "display": "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='%238B5CF6'%3E%3Cpath d='M21 2H3c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h7v2H8v2h8v-2h-2v-2h7c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zm0 14H3V4h18v12z'/%3E%3C/svg%3E",
    "id_card": "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='%2306B6D4'%3E%3Cpath d='M20 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm-9 3.5c1.38 0 2.5 1.12 2.5 2.5S12.38 12.5 11 12.5 8.5 11.38 8.5 10s1.12-2.5 2.5-2.5zm5 10.5H6v-1.25c0-1.66 3.33-2.5 5-2.5s5 .84 5 2.5V18zm2-4h-4v-2h4v2zm0-4h-4V8h4v2z'/%3E%3C/svg%3E",
    # Operations icons
    "chart_bar": "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='%233B82F6'%3E%3Cpath d='M5 9.2h3V19H5V9.2zM10.6 5h2.8v14h-2.8V5zm5.6 8H19v6h-2.8v-6z'/%3E%3C/svg%3E",
    "chart_line": "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='%2310B981'%3E%3Cpath d='M3.5 18.49l6-6.01 4 4L22 6.92l-1.41-1.41-7.09 7.97-4-4L2 16.99z'/%3E%3C/svg%3E",
    "analytics": "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='%23F59E0B'%3E%3Cpath d='M19 3H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zM9 17H7v-7h2v7zm4 0h-2V7h2v10zm4 0h-2v-4h2v4z'/%3E%3C/svg%3E",
    "brain": "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='%23A855F7'%3E%3Cpath d='M15.5 14l-1.34-4H9.84L8.5 14H6.09l4.01-10h3.8l4.01 10H15.5zm-4.5-5.4h2l.9-2.35.8 2.35h1.8l-1.45 1.05.55 1.7-1.4-1.02-1.4 1.02.55-1.7L11 8.6z'/%3E%3C/svg%3E",
    # AI icons
    "ai_brain": "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='%236366F1'%3E%3Cpath d='M21 10.12h-6.78l2.74-2.82c-2.73-2.7-7.15-2.8-9.88-.1-2.73 2.71-2.73 7.08 0 9.79s7.15 2.71 9.88 0C18.32 15.65 19 14.08 19 12.1h2c0 1.98-.88 4.55-2.64 6.29-3.51 3.48-9.21 3.48-12.72 0-3.5-3.47-3.53-9.11-.02-12.58s9.14-3.47 12.65 0L21 3v7.12zM12.5 8v4.25l3.5 2.08-.72 1.21L11 13V8h1.5z'/%3E%3C/svg%3E",
}

# =============================================================================
# Domain Icon Mapping - Maps each domain to its icon and SVG
# =============================================================================

DOMAIN_ICONS = {
    # Infrastructure
    "customer_edge": {"icon": "📡", "svg_key": "antenna"},
    "cloud_infrastructure": {"icon": "☁️", "svg_key": "cloud"},
    "container_services": {"icon": "📦", "svg_key": "box"},
    "managed_kubernetes": {"icon": "⚙️", "svg_key": "gear"},
    "service_mesh": {"icon": "🕸️", "svg_key": "web"},
    "sites": {"icon": "🌍", "svg_key": "globe"},
    "ce_management": {"icon": "🔧", "svg_key": "wrench"},
    "vpm_and_node_management": {"icon": "🖥️", "svg_key": "server"},
    "bigip": {"icon": "🏢", "svg_key": "building"},
    "nginx_one": {"icon": "🟢", "svg_key": "circle"},
    "object_storage": {"icon": "🗄️", "svg_key": "cabinet"},
    # Networking
    "dns": {"icon": "🌐", "svg_key": "globe_network"},
    "virtual": {"icon": "⚖️", "svg_key": "balance"},
    "cdn": {"icon": "🚀", "svg_key": "rocket"},
    "network": {"icon": "🔌", "svg_key": "plug"},
    # Security
    "bot_defense": {"icon": "🤖", "svg_key": "robot"},
    "api": {"icon": "🔐", "svg_key": "lock_key"},
    "network_security": {"icon": "🔒", "svg_key": "lock"},
    "certificates": {"icon": "📜", "svg_key": "scroll"},
    "blindfold": {"icon": "🔏", "svg_key": "sealed"},
    "ddos": {"icon": "🛑", "svg_key": "stop"},
    "rate_limiting": {"icon": "⏱️", "svg_key": "timer"},
    "shape": {"icon": "🎭", "svg_key": "mask"},
    "threat_campaign": {"icon": "⚠️", "svg_key": "warning"},
    "bot_and_threat_defense": {"icon": "🦠", "svg_key": "virus"},
    "secops_and_incident_response": {"icon": "🚨", "svg_key": "siren"},
    "data_and_privacy_security": {"icon": "🔐", "svg_key": "lock_key"},
    "client_side_defense": {"icon": "🖥️", "svg_key": "monitor"},
    # Platform
    "authentication": {"icon": "🔑", "svg_key": "key"},
    "users": {"icon": "👥", "svg_key": "people"},
    "support": {"icon": "🎫", "svg_key": "ticket"},
    "marketplace": {"icon": "🏪", "svg_key": "store"},
    "billing_and_usage": {"icon": "💳", "svg_key": "card"},
    "billing": {"icon": "💳", "svg_key": "card"},
    "admin_console_and_ui": {"icon": "🖥️", "svg_key": "display"},
    "admin": {"icon": "🖥️", "svg_key": "display"},
    "tenant_and_identity": {"icon": "🪪", "svg_key": "id_card"},
    "system": {"icon": "⚙️", "svg_key": "gear"},
    "label": {"icon": "🏷️", "svg_key": "ticket"},
    # Operations
    "observability": {"icon": "📊", "svg_key": "chart_bar"},
    "statistics": {"icon": "📈", "svg_key": "chart_line"},
    "telemetry_and_insights": {"icon": "📉", "svg_key": "analytics"},
    "data_intelligence": {"icon": "🧠", "svg_key": "brain"},
    # AI
    "ai_services": {"icon": "🤖", "svg_key": "ai_brain"},
}

# =============================================================================
# Primary Resources by Domain - Main resource types for each domain
# =============================================================================

DOMAIN_PRIMARY_RESOURCES = {
    "customer_edge": ["voltstack_site", "securemesh_site", "virtual_site"],
    "cloud_infrastructure": [
        "aws_vpc_site",
        "azure_vnet_site",
        "gcp_vpc_site",
        "cloud_credentials",
    ],
    "container_services": ["virtual_k8s", "workload", "pod_security_policy"],
    "managed_kubernetes": ["mk8s_cluster", "k8s_cluster_role", "container_registry"],
    "service_mesh": ["endpoint", "origin_pool", "service_discovery"],
    "sites": ["site", "virtual_site", "site_mesh_group"],
    "dns": ["dns_zone", "dns_domain", "dns_load_balancer"],
    "virtual": [
        "http_loadbalancer",
        "tcp_loadbalancer",
        "origin_pool",
        "healthcheck",
        "app_firewall",
        "service_policy",
        "malicious_user_detection",
    ],
    "cdn": ["cdn_loadbalancer", "cdn_origin_pool"],
    "network": ["virtual_network", "network_connector", "site_mesh_group"],
    "bot_defense": ["bot_defense_policy", "bot_defense_advanced_policy"],
    "api": ["api_definition", "api_endpoint", "api_rate_limit"],
    "network_security": ["network_policy", "forward_proxy_policy", "network_firewall"],
    "certificates": ["certificate", "ca_certificate", "certificate_chain"],
    "blindfold": ["blindfold_secret", "secret_policy", "policy_document"],
    "ddos": ["ddos_protection", "ddos_mitigation_rule"],
    "rate_limiting": ["rate_limiter", "rate_limiter_policy", "rate_limit_threshold"],
    "shape": ["shape_app_firewall", "shape_recognizer"],
    "threat_campaign": ["threat_campaign_policy"],
    "authentication": ["authentication_policy", "token", "api_credential"],
    "users": ["user", "user_role", "namespace_role"],
    "support": ["support_case", "alert", "audit_log"],
    "system": ["namespace", "tenant", "cluster"],
    "observability": ["log_receiver", "metrics_receiver", "alert_policy"],
    "statistics": ["dashboard", "saved_query"],
    "billing_and_usage": ["subscription", "quota", "usage_report"],
    "billing": ["subscription", "invoice", "payment_method"],
    "admin_console_and_ui": ["ui_component", "static_asset"],
    "admin": ["global_setting", "system_config"],
    "tenant_and_identity": ["user_profile", "session", "otp_policy"],
    "marketplace": ["marketplace_item", "subscription"],
    "bigip": ["bigip_pool", "bigip_device"],
    "nginx_one": ["nginx_config", "nginx_upstream"],
    "ai_services": ["ai_policy", "ai_gateway"],
    "object_storage": ["object_store", "bucket"],
    "bot_and_threat_defense": ["bot_defense_instance", "threat_category"],
    "ce_management": ["site_config", "fleet_config", "registration_token"],
    "data_and_privacy_security": ["sensitive_data_policy", "data_classification"],
    "secops_and_incident_response": ["mitigation_policy", "malicious_user_rule"],
    "vpm_and_node_management": ["node_config", "vpm_config"],
    "client_side_defense": ["csd_policy", "script_monitor"],
    "telemetry_and_insights": ["telemetry_receiver", "insight_query"],
    "data_intelligence": ["analytics_query", "data_export"],
    "label": ["label_group", "known_label"],
}


def get_domain_icon(domain: str) -> dict[str, str]:
    """Get icon and logo_svg for a domain.

    Args:
        domain: The domain name

    Returns:
        Dict with icon (emoji) and logo_svg (data URI)
    """
    icon_info = DOMAIN_ICONS.get(domain, {"icon": "📁", "svg_key": "gear"})
    return {
        "icon": icon_info["icon"],
        "logo_svg": SVG_ICONS.get(icon_info["svg_key"], SVG_ICONS["gear"]),
    }


def _load_resource_metadata() -> dict[str, dict[str, Any]]:
    """Load per-resource metadata from config/resource_metadata.yaml.

    Uses caching to avoid repeated file reads.

    Returns:
        Dictionary with 'resources' mapping resource names to their metadata,
        and '_defaults' containing default values for unconfigured resources.
    """
    cache_key = "resource_metadata"

    if cache_key in _CACHE:
        return _CACHE[cache_key]

    config_path = Path(__file__).parent.parent.parent / "config" / "resource_metadata.yaml"

    if not config_path.exists():
        _CACHE[cache_key] = {"_defaults": {}}
        return _CACHE[cache_key]

    try:
        with config_path.open() as f:
            config = yaml.safe_load(f) or {}
        resources = config.get("resources", {})
        resources["_defaults"] = config.get("defaults", {})
        _CACHE[cache_key] = resources
    except (yaml.YAMLError, OSError):
        _CACHE[cache_key] = {"_defaults": {}}

    return _CACHE[cache_key]


def get_resource_metadata(resource_name: str) -> dict[str, Any]:
    """Get metadata for a single resource.

    Args:
        resource_name: Name of the resource (e.g., 'http_loadbalancer')

    Returns:
        Resource metadata dictionary with all fields populated,
        using defaults for unconfigured resources.
    """
    resource_config = _load_resource_metadata()
    defaults = resource_config.get("_defaults", {})
    metadata = resource_config.get(resource_name, {})

    # Build metadata with defaults fallback
    return {
        "name": resource_name,
        "description": metadata.get(
            "description",
            f"{resource_name.replace('_', ' ').title()} resource",
        ),
        "description_short": metadata.get(
            "description_short",
            resource_name.replace("_", " ").title(),
        ),
        "tier": metadata.get("tier", defaults.get("tier", "Standard")),
        "icon": metadata.get("icon", defaults.get("icon", "📦")),
        "category": metadata.get("category", defaults.get("category", "Other")),
        "supports_logs": metadata.get(
            "supports_logs",
            defaults.get("supports_logs", False),
        ),
        "supports_metrics": metadata.get(
            "supports_metrics",
            defaults.get("supports_metrics", False),
        ),
        "dependencies": metadata.get(
            "dependencies",
            defaults.get("dependencies", {"required": [], "optional": []}),
        ),
        "relationship_hints": metadata.get(
            "relationship_hints",
            defaults.get("relationship_hints", []),
        ),
    }


def get_primary_resources_metadata(
    domain: str,
    spec: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Get primary resources with full metadata for a domain.

    When spec is provided, adds 'schema_components' and 'api_paths' to each
    resource dict by running the heuristic resolver and applying config overrides
    from resource_metadata.yaml. When spec is omitted, returns the configured
    resource metadata without resolution fields.

    Args:
        domain: The domain name (e.g., 'virtual', 'waf', 'dns')
        spec: Full merged OpenAPI spec for the domain (optional).
              When provided, schema_components and api_paths are resolved.

    Returns:
        List of resource metadata dicts. With spec: includes schema_components
        and api_paths. Without spec: includes the configured metadata fields.
    """
    resource_names = DOMAIN_PRIMARY_RESOURCES.get(domain, [])
    resources = [get_resource_metadata(name) for name in resource_names]

    if spec is None:
        return resources

    domain_paths = spec.get("paths", {})
    resource_config = _load_resource_metadata()

    heuristic_results = {name: resolve_resource(name, domain_paths) for name in resource_names}

    # Only apply config overrides where the heuristic returned empty.
    # Resource names can appear in multiple domains (e.g. origin_pool in
    # virtual and service_mesh). Restricting overrides to heuristic failures
    # prevents a flat config entry from being validated/applied in domains
    # where the heuristic already produces the correct result.
    config_overrides = {
        name: resource_config[name]
        for name in resource_names
        if name in resource_config
        and ("schema_components" in resource_config[name] or "api_paths" in resource_config[name])
        and not heuristic_results[name][0]
    }

    errors = validate_resource_mappings(heuristic_results, config_overrides, domain_paths, domain)
    if errors:
        raise ValueError(
            f"Resource mapping validation failed for domain '{domain}':\n" + "\n".join(errors)
        )

    for resource in resources:
        name = resource["name"]
        heuristic = heuristic_results.get(name, ([], []))
        if heuristic[0]:
            # Heuristic resolved — use it directly, skip config override.
            # This prevents a flat config entry from applying to domains
            # where the heuristic already finds the correct mapping.
            schema_components, api_paths = heuristic
        else:
            override_entry = resource_config.get(name, {})
            schema_components, api_paths = apply_overrides(heuristic, override_entry)
        resource.update({"schema_components": schema_components, "api_paths": api_paths})

    return resources


DOMAIN_METADATA = {
    # Infrastructure & Deployment
    "customer_edge": {
        "is_preview": False,
        "requires_tier": "Standard",
        "category": "Infrastructure",
        "use_cases": [
            "Configure customer edge nodes",
            "Manage edge node registration and lifecycle",
            "Control module management and upgrades",
            "Configure network interfaces and USB policies",
        ],
        "related_domains": ["sites", "cloud_infrastructure"],
    },
    "cloud_infrastructure": {
        "is_preview": False,
        "requires_tier": "Standard",
        "category": "Infrastructure",
        "use_cases": [
            "Connect to cloud providers (AWS, Azure, GCP)",
            "Manage cloud credentials and authentication",
            "Configure cloud connectivity and elastic provisioning",
            "Link and manage cloud regions",
        ],
        "related_domains": ["sites", "customer_edge"],
    },
    "container_services": {
        "is_preview": False,
        "requires_tier": "Advanced",
        "category": "Infrastructure",
        # Industry-standard naming (XCCS = XC Container Services)
        "short_name": "XCCS",
        "full_name": "XC Container Services",
        "legacy_name": "Virtual Kubernetes (vK8s)",
        "comparable_to": ["AWS ECS", "Azure Container Services", "Cloud Run"],
        "use_cases": [
            "Deploy XCCS (Container Services) namespaces for multi-tenant workloads",
            "Manage container workloads with simplified orchestration",
            "Configure distributed edge container deployments",
            "Run containerized applications without full K8s complexity",
        ],
        "related_domains": ["managed_kubernetes", "sites", "service_mesh"],
    },
    "managed_kubernetes": {
        "is_preview": False,
        "requires_tier": "Advanced",
        "category": "Infrastructure",
        # Industry-standard naming (XCKS = XC Kubernetes Service)
        "short_name": "XCKS",
        "full_name": "XC Kubernetes Service",
        "legacy_name": "AppStack",
        "comparable_to": ["AWS EKS", "Azure AKS", "Google GKE"],
        "use_cases": [
            "Manage XCKS (Managed Kubernetes) cluster RBAC and security",
            "Configure pod security policies and admission controllers",
            "Manage container registries for enterprise deployments",
            "Integrate with external Kubernetes clusters (EKS, AKS, GKE)",
        ],
        "related_domains": ["container_services", "sites", "service_mesh"],
    },
    "service_mesh": {
        "is_preview": False,
        "requires_tier": "Advanced",
        "category": "Infrastructure",
        "use_cases": [
            "Configure service mesh connectivity",
            "Manage endpoint discovery and routing",
            "Configure NFV services",
            "Define application settings and types",
        ],
        "related_domains": ["managed_kubernetes", "container_services", "virtual"],
    },
    "sites": {
        "is_preview": False,
        "requires_tier": "Standard",
        "category": "Infrastructure",
        # Customer Edge deployments: Cloud sites, XCKS (Managed Kubernetes), Mesh
        "deployment_types": ["Cloud Sites", "XCKS (Managed Kubernetes)", "Secure Mesh"],
        "use_cases": [
            "Deploy F5 XC across cloud providers (AWS, Azure, GCP)",
            "Manage XCKS (Managed Kubernetes) site deployments (formerly AppStack)",
            "Deploy Secure Mesh sites for networking-focused edge deployments",
            "Integrate external Kubernetes clusters as Customer Edge",
            "Configure AWS VPC, Azure VNet, and GCP VPC sites",
            "Manage virtual sites and site policies",
        ],
        "related_domains": ["cloud_infrastructure", "customer_edge", "managed_kubernetes"],
    },
    # Security - Core
    "api": {
        "is_preview": False,
        "requires_tier": "Advanced",
        "category": "Security",
        "use_cases": [
            "Discover and catalog APIs",
            "Test API security and behavior",
            "Manage API credentials",
            "Define API groups and testing policies",
        ],
        "related_domains": ["virtual", "network_security"],
    },
    "bot_defense": {
        "is_preview": False,
        "requires_tier": "Advanced",
        "category": "Security",
        "use_cases": [
            "Manage bot allowlists and defense policies",
            "Configure bot endpoints and infrastructure",
            "Integrate threat intelligence",
            "Manage mobile SDK for app protection",
        ],
        "related_domains": ["virtual", "network_security"],
    },
    "network_security": {
        "is_preview": False,
        "requires_tier": "Advanced",
        "category": "Security",
        "use_cases": [
            "Configure network firewall and ACL policies",
            "Manage NAT policies and port forwarding",
            "Configure policy-based routing",
            "Define network segments and policies",
            "Configure forward proxy policies",
        ],
        "related_domains": ["virtual", "api", "network"],
    },
    # Security - Advanced
    "blindfold": {
        "is_preview": False,
        "requires_tier": "Advanced",
        "category": "Security",
        "use_cases": [
            "Configure secret policies for encryption",
            "Manage sensitive data encryption",
            "Enforce data protection policies",
        ],
        "related_domains": ["client_side_defense", "certificates"],
    },
    "client_side_defense": {
        "is_preview": False,
        "requires_tier": "Advanced",
        "category": "Security",
        "use_cases": [
            "Protect user data in transit",
            "Define sensitive data policies",
            "Manage device identification",
            "Configure data privacy controls",
        ],
        "related_domains": ["blindfold", "virtual"],
    },
    "ddos": {
        "is_preview": False,
        "requires_tier": "Advanced",
        "category": "Security",
        "use_cases": [
            "Configure DDoS protection policies",
            "Monitor and analyze DDoS threats",
            "Configure infrastructure protection",
        ],
        "related_domains": ["network_security", "virtual"],
    },
    "dns": {
        "is_preview": False,
        "requires_tier": "Standard",
        "category": "Networking",
        "use_cases": [
            "Configure DNS load balancing",
            "Manage DNS zones and domains",
            "Configure DNS compliance policies",
            "Manage resource record sets (RRSets)",
        ],
        "related_domains": ["virtual", "network"],
    },
    "virtual": {
        "is_preview": False,
        "requires_tier": "Advanced",
        "category": "Networking",
        "use_cases": [
            "Configure HTTP/TCP/UDP load balancers",
            "Manage origin pools and services",
            "Configure virtual hosts and routing",
            "Define rate limiter and service policies",
            "Manage geo-location-based routing",
            "Configure proxy and forwarding policies",
            "Manage malware protection and threat campaigns",
            "Configure health checks and endpoint monitoring",
        ],
        "related_domains": ["dns", "service_policy", "network"],
    },
    "network": {
        "is_preview": False,
        "requires_tier": "Advanced",
        "category": "Networking",
        "use_cases": [
            "Configure BGP routing and ASN management",
            "Manage IPsec tunnels and IKE phases",
            "Configure network connectors and routes",
            "Manage SRv6 and subnetting",
            "Define segment connections and policies",
            "Configure IP prefix sets",
        ],
        "related_domains": ["virtual", "network_security", "dns"],
    },
    "cdn": {
        "is_preview": False,
        "requires_tier": "Advanced",
        "category": "Networking",
        "use_cases": [
            "Configure CDN load balancing",
            "Manage content delivery network services",
            "Configure caching policies",
            "Manage data delivery and distribution",
        ],
        "related_domains": ["virtual"],
    },
    # Operations & Monitoring
    "observability": {
        "is_preview": False,
        "requires_tier": "Standard",
        "category": "Operations",
        "use_cases": [
            "Configure synthetic monitoring",
            "Define monitoring and testing policies",
            "Manage observability dashboards",
        ],
        "related_domains": ["statistics", "support"],
    },
    "statistics": {
        "is_preview": False,
        "requires_tier": "Standard",
        "category": "Operations",
        "use_cases": [
            "Access flow statistics and analytics",
            "Manage alerts and alerting policies",
            "View logs and log receivers",
            "Generate reports and graphs",
            "Track topology and service discovery",
            "Monitor status at sites",
        ],
        "related_domains": ["observability", "support"],
    },
    "support": {
        "is_preview": False,
        "requires_tier": "Standard",
        "category": "Operations",
        "use_cases": [
            "Submit and manage support tickets",
            "Track customer support requests",
            "Access operational support documentation",
        ],
        "related_domains": ["statistics", "observability"],
    },
    # System & Management
    "authentication": {
        "is_preview": False,
        "requires_tier": "Standard",
        "category": "Platform",
        "use_cases": [
            "Configure authentication mechanisms",
            "Manage OIDC and OAuth providers",
            "Configure SCIM user provisioning",
            "Manage API credentials and access",
            "Configure account signup policies",
        ],
        "related_domains": ["system", "users"],
    },
    "system": {
        "is_preview": False,
        "requires_tier": "Standard",
        "category": "Platform",
        "use_cases": [
            "Manage tenant configuration",
            "Define RBAC policies and roles",
            "Manage namespaces and contacts",
            "Manage user accounts and groups",
            "Configure core system settings",
        ],
        "related_domains": ["authentication", "users", "admin"],
    },
    "users": {
        "is_preview": False,
        "requires_tier": "Standard",
        "category": "Platform",
        "use_cases": [
            "Manage user accounts and tokens",
            "Configure user identification",
            "Manage user settings and preferences",
            "Configure implicit and known labels",
        ],
        "related_domains": ["system", "admin"],
    },
    # Platform & Integrations
    "bigip": {
        "is_preview": False,
        "requires_tier": "Advanced",
        "category": "Platform",
        "use_cases": [
            "Manage BigIP F5 appliances",
            "Configure iRule scripts",
            "Manage data groups",
            "Integrate BigIP CNE",
        ],
        "related_domains": ["marketplace"],
    },
    "marketplace": {
        "is_preview": False,
        "requires_tier": "Advanced",
        "category": "Platform",
        "use_cases": [
            "Access third-party integrations and add-ons",
            "Manage marketplace extensions",
            "Configure Terraform and external integrations",
            "Manage TPM policies",
        ],
        "related_domains": ["bigip", "admin"],
    },
    "nginx_one": {
        "is_preview": False,
        "requires_tier": "Advanced",
        "category": "Platform",
        "use_cases": [
            "Manage NGINX One platform integrations",
            "Configure NGINX Plus instances",
            "Integrate NGINX configuration management",
        ],
        "related_domains": ["marketplace"],
    },
    # Advanced & Emerging
    "certificates": {
        "is_preview": False,
        "requires_tier": "Standard",
        "category": "Security",
        "use_cases": [
            "Manage SSL/TLS certificates",
            "Configure trusted CAs",
            "Manage certificate revocation lists (CRL)",
            "Configure certificate manifests",
        ],
        "related_domains": ["blindfold", "system"],
    },
    "ai_services": {
        "is_preview": True,
        "requires_tier": "Advanced",
        "category": "AI",
        "use_cases": [
            "Access AI-powered features",
            "Configure AI assistant policies",
            "Enable flow anomaly detection",
            "Manage AI data collection",
        ],
        "related_domains": [],
    },
    "object_storage": {
        "is_preview": False,
        "requires_tier": "Advanced",
        "category": "Platform",
        "use_cases": [
            "Manage object storage services",
            "Configure stored objects and buckets",
            "Manage storage policies",
        ],
        "related_domains": ["marketplace"],
    },
    "rate_limiting": {
        "is_preview": False,
        "requires_tier": "Advanced",
        "category": "Networking",
        "use_cases": [
            "Configure rate limiter policies",
            "Manage policer configurations",
            "Control traffic flow and queuing",
        ],
        "related_domains": ["virtual", "network_security"],
    },
    "shape": {
        "is_preview": False,
        "requires_tier": "Advanced",
        "category": "Security",
        "use_cases": [
            "Configure Shape Security policies",
            "Manage bot and threat prevention",
            "Configure SafeAP policies",
            "Enable threat recognition",
        ],
        "related_domains": ["bot_defense", "virtual"],
    },
    # UI & Platform Infrastructure
    "admin": {
        "is_preview": False,
        "requires_tier": "Standard",
        "category": "Platform",
        "use_cases": [
            "Configure administration console",
            "Manage navigation tiles and UI elements",
            "Configure static UI components",
        ],
        "related_domains": ["system", "users"],
    },
    "billing": {
        "is_preview": False,
        "requires_tier": "Standard",
        "category": "Platform",
        "use_cases": [
            "Manage billing and subscription",
            "Configure payment methods",
            "Track usage and invoices",
            "Manage plan transitions",
            "Monitor quota usage",
        ],
        "related_domains": ["system", "users"],
    },
    "label": {
        "is_preview": False,
        "requires_tier": "Standard",
        "category": "Platform",
        "use_cases": [
            "Manage resource labels and tagging",
            "Configure label policies",
            "Enable compliance tracking",
        ],
        "related_domains": ["system"],
    },
    "data_intelligence": {
        "is_preview": False,
        "requires_tier": "Standard",
        "category": "Operations",
        "use_cases": [
            "Analyze security and traffic data",
            "Generate intelligent insights from logs",
            "Configure data analytics policies",
        ],
        "related_domains": ["statistics", "observability"],
    },
    "telemetry_and_insights": {
        "is_preview": False,
        "requires_tier": "Standard",
        "category": "Operations",
        "use_cases": [
            "Collect and analyze telemetry data",
            "Generate actionable insights from metrics",
            "Configure telemetry collection policies",
        ],
        "related_domains": ["observability", "statistics"],
    },
    "threat_campaign": {
        "is_preview": False,
        "requires_tier": "Standard",
        "category": "Security",
        "use_cases": [
            "Track and analyze threat campaigns",
            "Monitor active threats and attack patterns",
            "Configure threat intelligence integration",
        ],
        "related_domains": ["bot_defense", "ddos"],
    },
    "vpm_and_node_management": {
        "is_preview": False,
        "requires_tier": "Standard",
        "category": "Platform",
        "use_cases": [
            "Manage Virtual Private Mesh (VPM) configuration",
            "Configure node lifecycle and management",
            "Monitor VPM and node status",
        ],
        "related_domains": ["sites", "system"],
    },
    # Additional Domains (Issue #182)
    "admin_console_and_ui": {
        "is_preview": False,
        "requires_tier": "Standard",
        "category": "Platform",
        "use_cases": [
            "Manage static UI components for admin console",
            "Deploy and retrieve UI assets within namespaces",
            "Configure console interface elements",
            "Manage custom UI component metadata",
        ],
        "related_domains": ["admin", "system"],
    },
    "billing_and_usage": {
        "is_preview": False,
        "requires_tier": "Standard",
        "category": "Platform",
        "use_cases": [
            "Manage subscription plans and billing transitions",
            "Configure payment methods and invoices",
            "Track resource quota usage across namespaces",
            "Monitor usage limits and capacity",
        ],
        "related_domains": ["system", "users"],
    },
    "bot_and_threat_defense": {
        "is_preview": False,
        "requires_tier": "Advanced",
        "category": "Security",
        "use_cases": [
            "Configure bot defense instances per namespace",
            "Manage TPM threat categories for classification",
            "Provision API keys for automated defense systems",
            "Integrate threat intelligence services",
        ],
        "related_domains": ["bot_defense", "shape", "virtual"],
    },
    "ce_management": {
        "is_preview": False,
        "requires_tier": "Standard",
        "category": "Infrastructure",
        "use_cases": [
            "Manage Customer Edge site lifecycle",
            "Configure network interfaces and fleet settings",
            "Handle site registration and token workflows",
            "Execute site upgrades with pre-upgrade checks",
        ],
        "related_domains": ["customer_edge", "sites"],
    },
    "data_and_privacy_security": {
        "is_preview": False,
        "requires_tier": "Advanced",
        "category": "Security",
        "use_cases": [
            "Configure sensitive data detection policies",
            "Define custom data types for PII classification",
            "Manage LMA region configurations",
            "Integrate geo-configurations for compliance",
        ],
        "related_domains": ["blindfold", "client_side_defense"],
    },
    "secops_and_incident_response": {
        "is_preview": False,
        "requires_tier": "Advanced",
        "category": "Security",
        "use_cases": [
            "Configure automated threat mitigation policies",
            "Define rules for malicious user detection",
            "Manage incident response workflows",
            "Apply blocking or rate limiting to threats",
        ],
        "related_domains": ["bot_defense", "virtual", "network_security"],
    },
    "tenant_and_identity": {
        "is_preview": False,
        "requires_tier": "Standard",
        "category": "Platform",
        "use_cases": [
            "Manage user profiles and notification preferences",
            "Configure session controls and OTP settings",
            "Handle identity management operations",
            "Process initial user access requests",
        ],
        "related_domains": ["users", "authentication", "system"],
    },
}


def get_metadata(domain: str) -> dict[str, Any]:
    """Get metadata for a specific domain, including CLI metadata if available.

    Args:
        domain: The domain name

    Returns:
        Dict with is_preview, requires_tier, category, use_cases, related_domains
        and optionally cli_metadata if available for the domain.
        Falls back to defaults if domain not explicitly configured.
    """
    metadata = DOMAIN_METADATA.get(
        domain,
        {
            "is_preview": False,
            "requires_tier": "Standard",
            "category": "Other",
        },
    )

    # Add CLI metadata if available
    cli_metadata = get_cli_metadata(domain)
    if cli_metadata:
        metadata["cli_metadata"] = cli_metadata

    return metadata


def get_all_metadata() -> dict[str, dict[str, Any]]:
    """Get metadata for all configured domains."""
    return DOMAIN_METADATA.copy()


def calculate_complexity(path_count: int, schema_count: int) -> str:
    """Calculate domain complexity based on API surface area.

    Formula: score = (path_count * 0.4) + (schema_count * 0.6)
    Schema count weighted higher (60%) as data model complexity
    impacts code generation more than endpoint count.

    Args:
        path_count: Number of API endpoints/paths in the domain
        schema_count: Number of schemas/data models in the domain

    Returns:
        Complexity level: "simple" | "moderate" | "advanced"

    Examples:
        >>> calculate_complexity(2, 16)  # admin domain
        'simple'
        >>> calculate_complexity(36, 228)  # api domain
        'moderate'
        >>> calculate_complexity(164, 1248)  # virtual domain
        'advanced'
    """
    score = (path_count * 0.4) + (schema_count * 0.6)

    if score < 50:
        return "simple"
    if score < 150:
        return "moderate"
    return "advanced"


CLI_METADATA = {
    "virtual": {
        "quick_start": {
            "command": "curl $F5XC_API_URL/api/config/namespaces/default/http_loadbalancers -H 'Authorization: APIToken $F5XC_API_TOKEN'",
            "description": "List all HTTP load balancers in default namespace",
            "expected_output": "JSON array of load balancer objects with status",
        },
        "common_workflows": [
            {
                "name": "Create HTTP Load Balancer",
                "description": "Deploy basic HTTP load balancer with origin pool backend",
                "steps": [
                    {
                        "step": 1,
                        "command": "curl -X POST $F5XC_API_URL/api/config/namespaces/default/origin_pools -H 'Authorization: APIToken $F5XC_API_TOKEN' -H 'Content-Type: application/json' -d '{...pool_config...}'",
                        "description": "Create backend origin pool with target endpoints",
                    },
                    {
                        "step": 2,
                        "command": "curl -X POST $F5XC_API_URL/api/config/namespaces/default/http_loadbalancers -H 'Authorization: APIToken $F5XC_API_TOKEN' -H 'Content-Type: application/json' -d '{...lb_config...}'",
                        "description": "Create HTTP load balancer pointing to origin pool",
                    },
                ],
                "prerequisites": [
                    "Active namespace",
                    "Origin pool targets reachable",
                    "DNS domain configured",
                ],
                "expected_outcome": "Load balancer in Active status, traffic routed to origins",
            },
        ],
        "troubleshooting": [
            {
                "problem": "Load balancer shows Configuration Error status",
                "symptoms": [
                    "Status: Configuration Error",
                    "No traffic routing",
                    "Requests timeout",
                ],
                "diagnosis_commands": [
                    "curl $F5XC_API_URL/api/config/namespaces/default/http_loadbalancers/{name} -H 'Authorization: APIToken $F5XC_API_TOKEN'",
                    "Check origin_pool status and endpoint connectivity",
                ],
                "solutions": [
                    "Verify origin pool targets are reachable from edge",
                    "Check DNS configuration and domain propagation",
                    "Validate certificate configuration if using HTTPS",
                    "Review security policies not blocking traffic",
                ],
            },
        ],
        "icon": "⚖️",
    },
    "dns": {
        "quick_start": {
            "command": "curl $F5XC_API_URL/api/config/namespaces/default/dns_domains -H 'Authorization: APIToken $F5XC_API_TOKEN'",
            "description": "List all DNS domains configured in default namespace",
            "expected_output": "JSON array of DNS domain objects",
        },
        "common_workflows": [
            {
                "name": "Create DNS Domain",
                "description": "Configure DNS domain with load balancer backend",
                "steps": [
                    {
                        "step": 1,
                        "command": "Create load balancer endpoint first (virtual domain)",
                        "description": "Ensure target load balancer exists",
                    },
                    {
                        "step": 2,
                        "command": "curl -X POST $F5XC_API_URL/api/config/namespaces/default/dns_domains -H 'Authorization: APIToken $F5XC_API_TOKEN' -H 'Content-Type: application/json' -d '{...dns_config...}'",
                        "description": "Create DNS domain pointing to load balancer",
                    },
                ],
                "prerequisites": [
                    "DNS domain registered",
                    "Load balancer configured",
                    "SOA and NS records prepared",
                ],
                "expected_outcome": "DNS domain in Active status, queries resolving to load balancer",
            },
        ],
        "troubleshooting": [
            {
                "problem": "DNS queries not resolving",
                "symptoms": ["NXDOMAIN responses", "Timeout on DNS queries"],
                "diagnosis_commands": [
                    "curl $F5XC_API_URL/api/config/namespaces/default/dns_domains/{domain} -H 'Authorization: APIToken $F5XC_API_TOKEN'",
                    "nslookup {domain} @ns-server",
                ],
                "solutions": [
                    "Verify domain delegation to F5 XC nameservers",
                    "Check DNS domain configuration and backend load balancer status",
                    "Validate zone file and record configuration",
                ],
            },
        ],
        "icon": "🌐",
    },
    "api": {
        "quick_start": {
            "command": "curl $F5XC_API_URL/api/config/namespaces/default/api_catalogs -H 'Authorization: APIToken $F5XC_API_TOKEN'",
            "description": "List all API catalogs in default namespace",
            "expected_output": "JSON array of API catalog objects",
        },
        "common_workflows": [
            {
                "name": "Protect API with Security Policy",
                "description": "Discover and protect APIs with WAF security policies",
                "steps": [
                    {
                        "step": 1,
                        "command": "curl -X POST $F5XC_API_URL/api/config/namespaces/default/api_catalogs -H 'Authorization: APIToken $F5XC_API_TOKEN' -H 'Content-Type: application/json' -d '{...catalog_config...}'",
                        "description": "Create API catalog for API discovery and documentation",
                    },
                    {
                        "step": 2,
                        "command": "curl -X POST $F5XC_API_URL/api/config/namespaces/default/api_definitions -H 'Authorization: APIToken $F5XC_API_TOKEN' -H 'Content-Type: application/json' -d '{...api_config...}'",
                        "description": "Create API definition with security enforcement",
                    },
                ],
                "prerequisites": [
                    "API endpoints documented",
                    "Security policies defined",
                    "WAF rules configured",
                ],
                "expected_outcome": "APIs protected, violations logged and blocked",
            },
        ],
        "troubleshooting": [
            {
                "problem": "API traffic blocked by security policy",
                "symptoms": ["HTTP 403 Forbidden", "Requests rejected at edge"],
                "diagnosis_commands": [
                    "curl $F5XC_API_URL/api/config/namespaces/default/api_definitions/{api} -H 'Authorization: APIToken $F5XC_API_TOKEN'",
                    "Check security policy enforcement rules",
                ],
                "solutions": [
                    "Review API definition and security policy rules",
                    "Adjust rule sensitivity to reduce false positives",
                    "Add exception rules for legitimate traffic patterns",
                ],
            },
        ],
        "icon": "🔐",
    },
    "sites": {
        "quick_start": {
            "command": "curl $F5XC_API_URL/api/config/namespaces/default/sites -H 'Authorization: APIToken $F5XC_API_TOKEN'",
            "description": "List all configured sites in default namespace",
            "expected_output": "JSON array of site objects with deployment status",
        },
        "common_workflows": [
            {
                "name": "Deploy AWS Cloud Site",
                "description": "Deploy F5 XC in AWS for traffic management",
                "steps": [
                    {
                        "step": 1,
                        "command": "curl -X POST $F5XC_API_URL/api/config/namespaces/default/cloud_credentials -H 'Authorization: APIToken $F5XC_API_TOKEN' -H 'Content-Type: application/json' -d '{...aws_credentials...}'",
                        "description": "Create cloud credentials for AWS access",
                    },
                    {
                        "step": 2,
                        "command": "curl -X POST $F5XC_API_URL/api/config/namespaces/default/sites -H 'Authorization: APIToken $F5XC_API_TOKEN' -H 'Content-Type: application/json' -d '{...site_config...}'",
                        "description": "Create site definition for AWS deployment",
                    },
                ],
                "prerequisites": [
                    "AWS account configured",
                    "Cloud credentials created",
                    "VPC and security groups prepared",
                ],
                "expected_outcome": "Site deployed in AWS, nodes connected and healthy",
            },
        ],
        "troubleshooting": [
            {
                "problem": "Site deployment fails",
                "symptoms": ["Status: Error", "Nodes not coming online", "Connectivity issues"],
                "diagnosis_commands": [
                    "curl $F5XC_API_URL/api/config/namespaces/default/sites/{site} -H 'Authorization: APIToken $F5XC_API_TOKEN'",
                    "Check site events and node status",
                ],
                "solutions": [
                    "Verify cloud credentials have required permissions",
                    "Check VPC and security group configuration",
                    "Review site logs for deployment errors",
                    "Ensure sufficient cloud resources available",
                ],
            },
        ],
        "icon": "🌍",
    },
    "system": {
        "quick_start": {
            "command": "curl $F5XC_API_URL/api/config/system/namespaces -H 'Authorization: APIToken $F5XC_API_TOKEN'",
            "description": "List all namespaces in the F5 XC system",
            "expected_output": "JSON array of namespace objects",
        },
        "common_workflows": [
            {
                "name": "Create Tenant Namespace",
                "description": "Create isolated namespace for tenant resources",
                "steps": [
                    {
                        "step": 1,
                        "command": "curl -X POST $F5XC_API_URL/api/config/system/namespaces -H 'Authorization: APIToken $F5XC_API_TOKEN' -H 'Content-Type: application/json' -d '{...namespace_config...}'",
                        "description": "Create namespace with appropriate quotas",
                    },
                    {
                        "step": 2,
                        "command": "curl -X POST $F5XC_API_URL/api/config/system/role_bindings -H 'Authorization: APIToken $F5XC_API_TOKEN' -H 'Content-Type: application/json' -d '{...role_config...}'",
                        "description": "Assign RBAC roles to namespace users",
                    },
                ],
                "prerequisites": [
                    "System admin access",
                    "User groups defined",
                    "Resource quotas planned",
                ],
                "expected_outcome": "Namespace created, users can access and manage resources",
            },
        ],
        "troubleshooting": [
            {
                "problem": "Users cannot access namespace resources",
                "symptoms": ["Permission denied errors", "Resources not visible"],
                "diagnosis_commands": [
                    "curl $F5XC_API_URL/api/config/system/namespaces/{ns} -H 'Authorization: APIToken $F5XC_API_TOKEN'",
                    "Check RBAC role bindings for namespace",
                ],
                "solutions": [
                    "Verify RBAC role bindings are correct",
                    "Check namespace quotas not exceeded",
                    "Review IAM policies for resource access",
                ],
            },
        ],
        "icon": "⚙️",
    },
}


def get_cli_metadata(domain: str) -> dict[str, Any] | None:
    """Get CLI metadata for a domain if available.

    Args:
        domain: The domain name

    Returns:
        Dict with quick_start, common_workflows, troubleshooting, icon
        or None if CLI metadata not available for this domain
    """
    return CLI_METADATA.get(domain)
