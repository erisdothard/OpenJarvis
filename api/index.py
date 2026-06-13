"""Vercel serverless entrypoint for OpenJarvis API."""

from openjarvis.core.config import load_config
from openjarvis.engine.cloud import CloudEngine
from openjarvis.server.app import create_app

config = load_config()
engine = CloudEngine()
model = config.engine.default_model

app = create_app(engine, model, config=config)
