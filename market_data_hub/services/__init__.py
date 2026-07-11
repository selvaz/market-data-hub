# -*- coding: utf-8 -*-
"""
services — the hub's public semantic layer (plan v3.1 §3.1).

CLI, notebooks, workers and the LLM adapter (agent_tools.py) all call these
same functions. Read paths (`resolve_*`, `get_*`) never touch the network;
ingestion is explicit via `ensure_*` capabilities that always run under the
writer lock with a persistent, idempotent job.
"""
