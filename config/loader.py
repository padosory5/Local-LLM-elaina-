from __future__ import annotations

import os
from pathlib import Path
from typing import Any, TypeVar

import yaml
from dotenv import load_dotenv


T = TypeVar("T")


class ConfigError(RuntimeError):
    """Raised when Elaina's configuration is missing or invalid."""


class Config:
    def __init__(
        self,
        config_path: str | Path | None = None,
    ) -> None:
        # Project root:
        # elainaAI/
        # ├── config/
        # │   ├── config.yaml
        # │   └── loader.py
        # └── .env
        self.project_root = Path(__file__).resolve().parent.parent

        load_dotenv(
            self.project_root / ".env",
            override=False,
        )

        if config_path is None:
            self.path = (
                Path(__file__).resolve().parent
                / "config.yaml"
            )
        else:
            supplied_path = Path(config_path)

            if not supplied_path.is_absolute():
                supplied_path = (
                    self.project_root / supplied_path
                )

            self.path = supplied_path.resolve()

        self.data = self._load_yaml()
        self._validate_root_sections()

    def _load_yaml(self) -> dict[str, Any]:
        if not self.path.is_file():
            raise ConfigError(
                f"Configuration file not found: {self.path}"
            )

        try:
            with self.path.open(
                "r",
                encoding="utf-8",
            ) as config_file:
                loaded = yaml.safe_load(config_file)

        except yaml.YAMLError as error:
            raise ConfigError(
                f"Invalid YAML in {self.path}: {error}"
            ) from error

        except OSError as error:
            raise ConfigError(
                f"Could not read {self.path}: {error}"
            ) from error

        if loaded is None:
            raise ConfigError(
                f"Configuration file is empty: {self.path}"
            )

        if not isinstance(loaded, dict):
            raise ConfigError(
                "The top level of config.yaml must be a mapping."
            )

        return loaded

    def _validate_root_sections(self) -> None:
        required_sections = {
            "llm",
            "tts",
            "stt",
            "vad",
            "memory",
            "search",
            "debug",
        }

        missing = sorted(
            required_sections - self.data.keys()
        )

        if missing:
            raise ConfigError(
                "Missing configuration section(s): "
                + ", ".join(missing)
            )

        self._validate_provider_section("llm")
        self._validate_provider_section("tts")
        self._validate_provider_section("stt")
        self._validate_provider_section("vad")
        self._validate_provider_section("search")

    def _validate_provider_section(
        self,
        section_name: str,
    ) -> None:
        section = self.get(section_name)

        if not isinstance(section, dict):
            raise ConfigError(
                f"Configuration section '{section_name}' "
                "must be a mapping."
            )

        provider = section.get("provider")

        if not isinstance(provider, str) or not provider.strip():
            raise ConfigError(
                f"'{section_name}.provider' must be "
                "a non-empty string."
            )

        provider = provider.strip()

        if provider not in section:
            raise ConfigError(
                f"Provider '{provider}' was selected for "
                f"'{section_name}', but no "
                f"'{section_name}.{provider}' section exists."
            )

    def get(
        self,
        *keys: str,
        default: Any = None,
        required: bool = True,
    ) -> Any:
        """
        Read a nested configuration value.

        Example:
            config.get("llm", "ollama", "model")

        Optional value:
            config.get(
                "avatar",
                "unity",
                "host",
                default="127.0.0.1",
                required=False,
            )
        """
        if not keys:
            return self.data

        value: Any = self.data
        traversed: list[str] = []

        for key in keys:
            traversed.append(key)

            if not isinstance(value, dict) or key not in value:
                if required:
                    path = ".".join(traversed)

                    raise ConfigError(
                        f"Missing configuration value: {path}"
                    )

                return default

            value = value[key]

        return value

    def section(
        self,
        *keys: str,
    ) -> dict[str, Any]:
        """
        Return a configuration section and ensure it is a mapping.
        """
        value = self.get(*keys)

        if not isinstance(value, dict):
            path = ".".join(keys)

            raise ConfigError(
                f"Configuration section '{path}' "
                "must be a mapping."
            )

        return value

    def active_provider(
        self,
        category: str,
    ) -> str:
        """
        Return the selected provider for a category.

        Example:
            config.active_provider("llm")
            config.active_provider("tts")
        """
        provider = self.get(
            category,
            "provider",
        )

        if not isinstance(provider, str):
            raise ConfigError(
                f"'{category}.provider' must be a string."
            )

        return provider.strip()

    def active_provider_config(
        self,
        category: str,
    ) -> dict[str, Any]:
        """
        Return the configuration of the selected provider.

        Example:
            provider_config = (
                config.active_provider_config("llm")
            )
        """
        provider = self.active_provider(category)

        return self.section(
            category,
            provider,
        )

    def get_env(
        self,
        *keys: str,
        required: bool = True,
        default: str | None = None,
    ) -> str | None:
        """
        Read the name of an environment variable from config.yaml,
        then return its value from the environment.

        Example YAML:
            api_key_env: "OPENAI_API_KEY"

        Example Python:
            api_key = config.get_env(
                "llm",
                "openai",
                "api_key_env",
            )
        """
        env_name = self.get(
            *keys,
            required=required,
            default=None,
        )

        if env_name is None:
            return default

        if not isinstance(env_name, str):
            path = ".".join(keys)

            raise ConfigError(
                f"Environment variable name at '{path}' "
                "must be a string."
            )

        env_name = env_name.strip()

        if not env_name:
            if required:
                raise ConfigError(
                    "Environment variable name cannot be empty."
                )

            return default

        value = os.getenv(env_name)

        if value is None or not value.strip():
            if required:
                raise ConfigError(
                    f"Required environment variable "
                    f"'{env_name}' is not set."
                )

            return default

        return value.strip()

    def resolve_path(
        self,
        *keys: str,
        must_exist: bool = False,
    ) -> Path:
        """
        Read a path from the configuration.

        Relative paths are resolved from the project root.
        """
        raw_path = self.get(*keys)

        if not isinstance(raw_path, str) or not raw_path.strip():
            path = ".".join(keys)

            raise ConfigError(
                f"Configuration path '{path}' "
                "must be a non-empty string."
            )

        resolved = Path(raw_path).expanduser()

        if not resolved.is_absolute():
            resolved = (
                self.project_root / resolved
            )

        resolved = resolved.resolve()

        if must_exist and not resolved.exists():
            path = ".".join(keys)

            raise ConfigError(
                f"Configured path does not exist "
                f"for '{path}': {resolved}"
            )

        return resolved

    def reload(self) -> None:
        """
        Reload config.yaml from disk.
        """
        self.data = self._load_yaml()
        self._validate_root_sections()