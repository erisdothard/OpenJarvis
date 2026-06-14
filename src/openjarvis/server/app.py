"""FastAPI application factory for the OpenJarvis API server."""

from __future__ import annotations

import asyncio
import logging
import pathlib
import time

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from openjarvis.server.analytics_routes import router as analytics_router
from openjarvis.server.api_routes import include_all_routes
from openjarvis.server.comparison import comparison_router
from openjarvis.server.connectors_router import create_connectors_router
from openjarvis.server.dashboard import dashboard_router
from openjarvis.server.glance_routes import glance_router
from openjarvis.server.digest_routes import create_digest_router
from openjarvis.server.research_router import router as research_router
from openjarvis.server.routes import router
from openjarvis.server.upload_router import router as upload_router
from openjarvis.server.voice_ws import router as voice_ws_router

logger = logging.getLogger(__name__)


def _restore_sendblue_bindings(app: FastAPI) -> None:
    """Restore SendBlue channel bindings from the database on startup.

    If a SendBlue binding was created via the Messaging tab and the server
    restarts, this ensures the ChannelBridge + DeepResearchAgent are wired
    up so incoming webhooks continue to work.
    """
    try:
        mgr = getattr(app.state, "agent_manager", None)
        if mgr is None:
            return

        # Check all agents for sendblue bindings
        for agent in mgr.list_agents():
            agent_id = agent.get("id", agent.get("agent_id", ""))
            bindings = mgr.list_channel_bindings(agent_id)
            for b in bindings:
                if b.get("channel_type") != "sendblue":
                    continue
                config = b.get("config", {})
                api_key_id = config.get("api_key_id", "")
                api_secret_key = config.get("api_secret_key", "")
                from_number = config.get("from_number", "")
                if not api_key_id or not api_secret_key:
                    continue

                from openjarvis.channels.sendblue import SendBlueChannel

                sb = SendBlueChannel(
                    api_key_id=api_key_id,
                    api_secret_key=api_secret_key,
                    from_number=from_number,
                )
                sb.connect()
                app.state.sendblue_channel = sb

                # Create ChannelBridge if none exists
                bridge = getattr(app.state, "channel_bridge", None)
                if bridge and hasattr(bridge, "_channels"):
                    bridge._channels["sendblue"] = sb
                else:
                    from openjarvis.server.channel_bridge import ChannelBridge
                    from openjarvis.server.session_store import SessionStore

                    session_store = SessionStore()
                    engine = getattr(app.state, "engine", None)
                    dr_agent = None
                    if engine:
                        from openjarvis.server.agent_manager_routes import (
                            _build_deep_research_tools,
                        )

                        tools = _build_deep_research_tools(engine=engine, model="")
                        if tools:
                            from openjarvis.agents.deep_research import (
                                DeepResearchAgent,
                            )

                            model_name = getattr(app.state, "model", "") or getattr(
                                engine, "_model", ""
                            )
                            dr_agent = DeepResearchAgent(
                                engine=engine,
                                model=model_name,
                                tools=tools,
                            )

                    bus = getattr(app.state, "bus", None)
                    if bus is None:
                        from openjarvis.core.events import EventBus

                        bus = EventBus()

                    app.state.channel_bridge = ChannelBridge(
                        channels={"sendblue": sb},
                        session_store=session_store,
                        bus=bus,
                        agent_manager=mgr,
                        deep_research_agent=dr_agent,
                    )

                logger.info(
                    "Restored SendBlue channel binding: %s",
                    from_number,
                )
                return  # Only need one SendBlue binding
    except Exception as exc:
        logger.debug("SendBlue binding restore skipped: %s", exc)


# No-cache headers applied to static file responses
_NO_CACHE_HEADERS = {
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "Pragma": "no-cache",
    "Expires": "0",
}


class _NoCacheStaticFiles(StaticFiles):
    """StaticFiles subclass that adds no-cache headers to every response."""

    async def __call__(self, scope, receive, send):
        async def _send_with_headers(message):
            if message["type"] == "http.response.start":
                extra = [(k.encode(), v.encode()) for k, v in _NO_CACHE_HEADERS.items()]
                # Remove etag and last-modified
                existing = [
                    (k, v)
                    for k, v in message.get("headers", [])
                    if k.lower() not in (b"etag", b"last-modified")
                ]
                message = {**message, "headers": existing + extra}
            await send(message)

        await super().__call__(scope, receive, _send_with_headers)


def create_app(
    engine,
    model: str,
    *,
    agent=None,
    bus=None,
    engine_name: str = "",
    agent_name: str = "",
    channel_bridge=None,
    config=None,
    memory_backend=None,
    speech_backend=None,
    agent_manager=None,
    agent_scheduler=None,
    telem_store=None,
    api_key: str = "",
    webhook_config: dict | None = None,
    cors_origins: list[str] | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application.

    Parameters
    ----------
    engine:
        The inference engine to use for completions.
    model:
        Default model name.
    agent:
        Optional agent instance for agent-mode completions.
    bus:
        Optional event bus for telemetry.
    channel_bridge:
        Optional channel bridge for multi-platform messaging.
    config:
        Optional JarvisConfig for other settings.
    """
    app = FastAPI(
        title="OpenJarvis API",
        description="OpenAI-compatible API server for OpenJarvis",
        version="0.1.0",
    )

    from fastapi.middleware.cors import CORSMiddleware

    _origins = (
        cors_origins
        if cors_origins is not None
        else [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:5174",
            "http://127.0.0.1:5174",
            # Tauri 2 production webview origins:
            #   macOS / Linux / iOS  -> tauri://localhost
            #   Windows / Android    -> http://tauri.localhost (default),
            #                           https://tauri.localhost when
            #                           windows.useHttpsScheme is enabled
            "tauri://localhost",
            "http://tauri.localhost",
            "https://tauri.localhost",
        ]
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Store dependencies in app state
    app.state.engine = engine
    app.state.model = model
    app.state.agent = agent
    app.state.bus = bus
    app.state.engine_name = engine_name
    app.state.agent_name = agent_name or (
        getattr(agent, "agent_id", None) if agent else None
    )
    app.state.channel_bridge = channel_bridge
    app.state.config = config
    app.state.memory_backend = memory_backend
    app.state.speech_backend = speech_backend
    app.state.agent_manager = agent_manager
    app.state.agent_scheduler = agent_scheduler
    app.state.telem_store = telem_store
    app.state.session_start = time.time()

    # Shared system-prompt cache — avoids re-reading SOUL/MEMORY/USER.md on
    # every HTTP request.  Staleness is detected via file mtime (stat only).
    from openjarvis.prompt.builder import SystemPromptCache
    app.state.prompt_cache = SystemPromptCache()

    # Pre-warm TTS backend so the first voice response has no cold-start lag
    try:
        from openjarvis.core.registry import TTSRegistry
        import openjarvis.speech.kokoro_tts  # noqa: F401
        tts_cache: dict = {}
        if TTSRegistry.contains("kokoro"):
            backend = TTSRegistry.get("kokoro")()
            backend.synthesize("warm", voice_id="af_heart")
            tts_cache["kokoro"] = backend
        app.state._tts_cache = tts_cache
    except Exception:
        app.state._tts_cache = {}
    # Exposed so WebSocket handlers can authenticate the handshake (the HTTP
    # AuthMiddleware never sees WS upgrade requests). Empty = auth disabled.
    app.state.api_key = api_key

    # Wire up trace store if traces are enabled.
    #
    # We deliberately do NOT subscribe the trace store to the bus. The chat
    # endpoints persist through a TraceCollector that calls store.save()
    # directly (mirroring system/orchestrator.py), and the collector ALSO
    # publishes TRACE_COMPLETE. A store subscribed to that same bus would
    # therefore save every agent trace twice — the second INSERT hitting the
    # UNIQUE constraint on trace_id (a 500 on every completion). Keeping the
    # collector the single writer is what makes the dual code path safe; only
    # the telemetry store is bus-subscribed (see system/builder.py).
    app.state.trace_store = None
    try:
        from openjarvis.core.config import load_config
        from openjarvis.traces.store import TraceStore

        cfg = config if config is not None else load_config()
        if cfg.traces.enabled:
            app.state.trace_store = TraceStore(db_path=cfg.traces.db_path)
    except Exception:
        pass  # traces are optional; don't block server startup

    # Wire up external analytics if enabled (PostHog) — never block startup.
    # Note: we do NOT fire app_opened here. The frontend owns that event
    # because "server started" (this code path) is not the same as "user
    # opened the app" — the server can run headless via cron, daemons,
    # or test suites.
    app.state.analytics_client = None
    app.state.analytics_bridge = None
    try:
        from openjarvis.analytics import (
            AnalyticsClient,
            EventBridge,
            is_analytics_enabled,
        )
        from openjarvis.core.config import load_config

        _cfg = config if config is not None else load_config()
        if is_analytics_enabled(_cfg.analytics):
            _client = AnalyticsClient(_cfg.analytics)
            app.state.analytics_client = _client
            _bus_ref = getattr(app.state, "bus", None)
            if _bus_ref is not None:
                _bridge = EventBridge(_bus_ref, _client)
                _bridge.start()
                app.state.analytics_bridge = _bridge

            @app.on_event("shutdown")
            async def _shutdown_analytics() -> None:
                bridge = getattr(app.state, "analytics_bridge", None)
                if bridge is not None:
                    try:
                        bridge.stop()
                    except Exception:
                        pass
                client = getattr(app.state, "analytics_client", None)
                if client is not None:
                    try:
                        client.shutdown()
                    except Exception:
                        pass
    except Exception as _exc:
        logger.debug("Analytics init skipped: %s", _exc)

    app.include_router(router)
    app.include_router(dashboard_router)
    app.include_router(comparison_router)
    app.include_router(create_connectors_router())
    app.include_router(create_digest_router())
    app.include_router(upload_router)
    app.include_router(research_router)
    app.include_router(analytics_router)
    app.include_router(glance_router)
    app.include_router(voice_ws_router)

    include_all_routes(app)

    # Desktop alerting on agent failures
    if bus is not None:
        try:
            from openjarvis.agents.alerting import AlertSubscriber

            app.state.alert_subscriber = AlertSubscriber(bus)
        except Exception as exc:
            logger.debug("Alert subscriber init skipped: %s", exc)

    # Restore SendBlue channel bindings from database on startup
    _restore_sendblue_bindings(app)

    # Add security headers middleware
    try:
        from openjarvis.server.middleware import create_security_middleware

        middleware_cls = create_security_middleware()
        if middleware_cls is not None:
            app.add_middleware(middleware_cls)
    except Exception as exc:
        logger.debug("Security middleware init skipped: %s", exc)

    # API key authentication middleware
    if api_key:
        try:
            from openjarvis.server.auth_middleware import AuthMiddleware

            app.add_middleware(AuthMiddleware, api_key=api_key)
        except Exception as exc:
            logger.debug("Auth middleware init skipped: %s", exc)

    # Mount webhook routes (always — SendBlue may be configured dynamically)
    if webhook_config:
        try:
            from openjarvis.server.webhook_routes import (
                create_webhook_router,
            )

            webhook_router = create_webhook_router(
                bridge=channel_bridge,
                bluebubbles_password=webhook_config.get("bluebubbles_password", ""),
                whatsapp_verify_token=webhook_config.get("whatsapp_verify_token", ""),
                whatsapp_app_secret=webhook_config.get("whatsapp_app_secret", ""),
            )
            app.include_router(webhook_router)
        except Exception as exc:
            logger.debug("Webhook routes init skipped: %s", exc)

    # -- Gmail Pub/Sub push listener ----------------------------------------
    @app.on_event("startup")
    async def _start_gmail_push() -> None:
        cfg = config if config is not None else load_config()
        if not cfg.alerts.enabled:
            return
        gp = cfg.alerts.gmail_push
        if not (gp.gcp_project and gp.topic and gp.subscription):
            return
        try:
            from openjarvis.server.gmail_push import (
                setup_gmail_watch,
                start_gmail_listener,
            )

            topic_full = f"projects/{gp.gcp_project}/topics/{gp.topic}"
            history_id = setup_gmail_watch(topic_full)
            if history_id:
                sa_path = gp.service_account or ""
                if sa_path and not Path(sa_path).is_absolute():
                    sa_path = str(DEFAULT_CONFIG_DIR / "connectors" / sa_path)
                start_gmail_listener(
                    gcp_project=gp.gcp_project,
                    subscription=gp.subscription,
                    phone="telegram",  # notifications route through Telegram
                    important_senders=gp.important_senders,
                    service_account_path=sa_path or None,
                )
                # Schedule watch renewal every 6 days
                async def _renew_loop() -> None:
                    while True:
                        await asyncio.sleep(6 * 24 * 3600)
                        try:
                            setup_gmail_watch(topic_full)
                        except Exception:
                            pass

                app.state._gmail_watch_task = asyncio.create_task(_renew_loop())
        except Exception as exc:
            logger.debug("Gmail push listener init skipped: %s", exc)

    @app.on_event("shutdown")
    async def _stop_gmail_push() -> None:
        task = getattr(app.state, "_gmail_watch_task", None)
        if task is not None:
            task.cancel()
        try:
            from openjarvis.server.gmail_push import stop_gmail_listener

            stop_gmail_listener()
        except Exception:
            pass

    # -- WAL checkpoint background task ------------------------------------
    # Periodically checkpoints all SQLite WAL files to prevent unbounded
    # WAL growth and keep read performance stable.
    @app.on_event("startup")
    async def _start_wal_checkpointer() -> None:
        from openjarvis.core.config import DEFAULT_CONFIG_DIR
        from openjarvis.core.db import checkpoint_all

        async def _loop() -> None:
            while True:
                await asyncio.sleep(300)  # every 5 minutes
                try:
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(
                        None, checkpoint_all, str(DEFAULT_CONFIG_DIR)
                    )
                except asyncio.CancelledError:
                    break
                except Exception:
                    pass

        app.state._checkpoint_task = asyncio.create_task(_loop())

    @app.on_event("shutdown")
    async def _stop_wal_checkpointer() -> None:
        task = getattr(app.state, "_checkpoint_task", None)
        if task is not None:
            task.cancel()

    # -- Daily database maintenance task -----------------------------------
    # Runs VACUUM, FTS optimize, and row purges once per day.
    # First run is delayed 1 hour after startup to avoid contending with
    # the startup I/O burst.  Subsequent runs fire every 24 hours.
    @app.on_event("startup")
    async def _start_daily_maintenance() -> None:
        from openjarvis.core.config import DEFAULT_CONFIG_DIR
        from openjarvis.core.maintenance import run_daily_maintenance

        async def _loop() -> None:
            # Initial delay — don't compete with startup I/O.
            await asyncio.sleep(3600)
            while True:
                try:
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(
                        None, run_daily_maintenance, DEFAULT_CONFIG_DIR
                    )
                except asyncio.CancelledError:
                    break
                except Exception as exc:
                    logger.warning("Daily maintenance error: %s", exc)
                try:
                    await asyncio.sleep(86400)  # 24 hours
                except asyncio.CancelledError:
                    break

        app.state._maintenance_task = asyncio.create_task(_loop())

    @app.on_event("shutdown")
    async def _stop_daily_maintenance() -> None:
        task = getattr(app.state, "_maintenance_task", None)
        if task is not None:
            task.cancel()

    @app.on_event("shutdown")
    async def _shutdown_databases() -> None:
        """Close all database-holding objects and run a final WAL TRUNCATE."""
        import sqlite3 as _sqlite3

        from openjarvis.core.config import DEFAULT_CONFIG_DIR

        # --- Close each app.state DB-holder ---
        def _try_close(obj) -> None:
            if obj is None:
                return
            try:
                obj.close()
            except Exception:
                pass

        _try_close(getattr(app.state, "trace_store", None))
        _try_close(getattr(app.state, "memory_backend", None))
        _try_close(getattr(app.state, "telem_store", None))

        agent_manager = getattr(app.state, "agent_manager", None)
        if agent_manager is not None:
            try:
                agent_manager.close()
            except Exception:
                pass

        # --- Final TRUNCATE checkpoint on all .db files ---
        # TRUNCATE moves WAL pages into the main DB file and resets the WAL
        # so the next startup has zero WAL to recover, giving faster boot and
        # preventing stale WAL accumulation across restarts.
        loop = asyncio.get_event_loop()

        def _truncate_all() -> None:
            for db_file in DEFAULT_CONFIG_DIR.rglob("*.db"):
                try:
                    conn = _sqlite3.connect(str(db_file))
                    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    conn.close()
                except _sqlite3.Error:
                    pass

        try:
            await loop.run_in_executor(None, _truncate_all)
        except Exception:
            pass

    # Serve static frontend assets if the static/ directory exists
    static_dir = pathlib.Path(__file__).parent / "static"
    if static_dir.is_dir():
        assets_dir = static_dir / "assets"
        if assets_dir.is_dir():
            app.mount(
                "/assets",
                _NoCacheStaticFiles(directory=assets_dir),
                name="static-assets",
            )

        @app.get("/{full_path:path}")
        async def spa_catch_all(full_path: str):
            """Serve static files directly, fall back to index.html for SPA routes."""
            if full_path:
                candidate = (static_dir / full_path).resolve()
                # Path traversal prevention
                resolved_root = static_dir.resolve()
                if candidate.is_relative_to(resolved_root) and candidate.is_file():
                    return FileResponse(candidate, headers=_NO_CACHE_HEADERS)
            return FileResponse(
                static_dir / "index.html",
                headers=_NO_CACHE_HEADERS,
            )

    return app


__all__ = ["create_app"]
