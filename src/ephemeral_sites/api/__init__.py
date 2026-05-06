"""HTTP API surface for ephemeral-sites.

Split across:

- :mod:`ephemeral_sites.api.app` — the FastAPI factory
- :mod:`ephemeral_sites.api.deps` — request dependencies
- :mod:`ephemeral_sites.api.errors` — exception handlers
- :mod:`ephemeral_sites.api.middleware` — request-id middleware
- :mod:`ephemeral_sites.api.models` — Pydantic request/response schemas
- :mod:`ephemeral_sites.api.routes_sites` — the PUT route (step 8)
"""
