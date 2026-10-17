"""Consumer configuration (environment-based; no secrets in source control)."""

from trafficfuse.config.settings import Settings, load_env_file

__all__ = ["Settings", "load_env_file"]
