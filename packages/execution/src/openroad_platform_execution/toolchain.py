from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping


TOOLCHAIN_SCHEMA_VERSION = 1
DEFAULT_INHERITED_ENVIRONMENT = (
    "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TZ", "TMPDIR", "TEMP", "TMP",
    "LD_LIBRARY_PATH", "LIBRARY_PATH", "TCL_LIBRARY", "TK_LIBRARY",
    "SSL_CERT_FILE", "SSL_CERT_DIR",
)
SYSTEM_PATH = ("/usr/local/sbin", "/usr/local/bin", "/usr/sbin", "/usr/bin", "/sbin", "/bin")


def _path(value: str | Path, *, base_dir: Path | None = None) -> Path:
    result = Path(value).expanduser()
    if not result.is_absolute() and base_dir is not None:
        result = base_dir / result
    return result.resolve()


def _deduplicate(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


@dataclass(frozen=True)
class ToolchainConfig:
    """One immutable selection of ORFS and its EDA executables."""

    name: str
    orfs_root: Path
    openroad_bin: Path
    yosys_bin: Path
    klayout_bin: Path | None = None
    extra_path: tuple[Path, ...] = ()
    environment: Mapping[str, str] = field(default_factory=dict)
    inherit_environment: tuple[str, ...] = DEFAULT_INHERITED_ENVIRONMENT

    @classmethod
    def from_environment(
        cls,
        *,
        name: str = "default",
        orfs_root: str | Path | None = None,
        openroad_bin: str | Path | None = None,
        yosys_bin: str | Path | None = None,
        klayout_bin: str | Path | None = None,
    ) -> "ToolchainConfig":
        home = Path.home()
        return cls(
            name=name,
            orfs_root=_path(orfs_root or os.environ.get("ORFS_ROOT") or home / "OpenROAD-flow-scripts"),
            openroad_bin=_path(openroad_bin or os.environ.get("OPENROAD_BIN") or home / "bin/openroad"),
            yosys_bin=_path(yosys_bin or os.environ.get("YOSYS_BIN") or home / "bin/yosys"),
            klayout_bin=_path(klayout_bin or os.environ.get("KLAYOUT_BIN") or home / "bin/klayout"),
        )

    @classmethod
    def from_dict(
        cls, name: str, payload: Mapping[str, Any], *, base_dir: Path
    ) -> "ToolchainConfig":
        missing = [key for key in ("orfs_root", "openroad_bin", "yosys_bin") if not payload.get(key)]
        if missing:
            raise ValueError(f"Toolchain {name!r} is missing: {', '.join(missing)}")
        environment = payload.get("environment", {})
        if not isinstance(environment, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in environment.items()
        ):
            raise ValueError(f"Toolchain {name!r} environment must be a string map")
        inherited = payload.get("inherit_environment", list(DEFAULT_INHERITED_ENVIRONMENT))
        extra_path = payload.get("extra_path", [])
        if not isinstance(inherited, list) or not all(isinstance(item, str) for item in inherited):
            raise ValueError(f"Toolchain {name!r} inherit_environment must be a string list")
        if not isinstance(extra_path, list) or not all(isinstance(item, str) for item in extra_path):
            raise ValueError(f"Toolchain {name!r} extra_path must be a string list")
        klayout = payload.get("klayout_bin")
        return cls(
            name=name,
            orfs_root=_path(str(payload["orfs_root"]), base_dir=base_dir),
            openroad_bin=_path(str(payload["openroad_bin"]), base_dir=base_dir),
            yosys_bin=_path(str(payload["yosys_bin"]), base_dir=base_dir),
            klayout_bin=_path(str(klayout), base_dir=base_dir) if klayout else None,
            extra_path=tuple(_path(item, base_dir=base_dir) for item in extra_path),
            environment=dict(environment),
            inherit_environment=tuple(inherited),
        )

    def with_overrides(
        self,
        *,
        orfs_root: str | Path | None = None,
        openroad_bin: str | Path | None = None,
        yosys_bin: str | Path | None = None,
        klayout_bin: str | Path | None = None,
    ) -> "ToolchainConfig":
        return replace(
            self,
            orfs_root=_path(orfs_root) if orfs_root else self.orfs_root,
            openroad_bin=_path(openroad_bin) if openroad_bin else self.openroad_bin,
            yosys_bin=_path(yosys_bin) if yosys_bin else self.yosys_bin,
            klayout_bin=_path(klayout_bin) if klayout_bin else self.klayout_bin,
        )

    @property
    def flow_home(self) -> Path:
        return self.orfs_root / "flow"

    def validate(self) -> None:
        if not self.name or any(char.isspace() for char in self.name):
            raise ValueError(f"Invalid toolchain name: {self.name!r}")
        makefile = self.flow_home / "Makefile"
        if not makefile.is_file():
            raise FileNotFoundError(f"ORFS Makefile not found: {makefile}")
        for name, binary in (("OpenROAD", self.openroad_bin), ("Yosys", self.yosys_bin)):
            if not binary.is_file() or not os.access(binary, os.X_OK):
                raise FileNotFoundError(f"{name} executable not found: {binary}")
        if self.klayout_bin is not None and (
            not self.klayout_bin.is_file() or not os.access(self.klayout_bin, os.X_OK)
        ):
            raise FileNotFoundError(f"KLayout executable not found: {self.klayout_bin}")

    def build_environment(
        self,
        *,
        source: Mapping[str, str] | None = None,
        extra: Mapping[str, str] | None = None,
        extra_path: tuple[str | Path, ...] = (),
    ) -> dict[str, str]:
        host = source if source is not None else os.environ
        env = {key: host[key] for key in self.inherit_environment if key in host}
        env.setdefault("HOME", str(Path.home()))
        paths = [str(self.openroad_bin.parent), str(self.yosys_bin.parent)]
        if self.klayout_bin is not None:
            paths.append(str(self.klayout_bin.parent))
        paths.extend(str(path) for path in self.extra_path)
        paths.extend(str(Path(path).expanduser()) for path in extra_path)
        paths.extend((str(Path.home() / ".local/bin"), *SYSTEM_PATH))
        env["PATH"] = os.pathsep.join(_deduplicate(paths))
        env.update(self.environment)
        if extra:
            env.update({str(key): str(value) for key, value in extra.items()})
        env["OPENROAD_PLATFORM_TOOLCHAIN"] = self.name
        return env

    def fingerprint(self) -> str:
        payload = {
            "name": self.name,
            "orfs_root": str(self.orfs_root),
            "openroad_bin": str(self.openroad_bin),
            "yosys_bin": str(self.yosys_bin),
            "klayout_bin": str(self.klayout_bin) if self.klayout_bin else None,
            "extra_path": [str(path) for path in self.extra_path],
            "inherit_environment": list(self.inherit_environment),
            "environment": dict(sorted(self.environment.items())),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": TOOLCHAIN_SCHEMA_VERSION,
            "name": self.name,
            "orfs_root": str(self.orfs_root),
            "openroad_bin": str(self.openroad_bin),
            "yosys_bin": str(self.yosys_bin),
            "klayout_bin": str(self.klayout_bin) if self.klayout_bin else None,
            "extra_path": [str(path) for path in self.extra_path],
            "inherit_environment": list(self.inherit_environment),
            "environment_keys": sorted(self.environment),
            "fingerprint": self.fingerprint(),
        }


class ToolchainCatalog:
    def __init__(self, profiles: Mapping[str, ToolchainConfig], *, default: str):
        if not profiles:
            raise ValueError("A toolchain catalog cannot be empty")
        if default not in profiles:
            raise ValueError(f"Unknown default toolchain: {default}")
        self._profiles = dict(profiles)
        self.default = default

    @classmethod
    def single(cls, config: ToolchainConfig) -> "ToolchainCatalog":
        return cls({config.name: config}, default=config.name)

    @classmethod
    def from_file(cls, path: str | Path) -> "ToolchainCatalog":
        config_path = Path(path).expanduser().resolve()
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != TOOLCHAIN_SCHEMA_VERSION:
            raise ValueError("Unsupported toolchain catalog schema_version")
        raw_profiles = payload.get("profiles")
        if not isinstance(raw_profiles, dict) or not raw_profiles:
            raise ValueError("Toolchain catalog profiles must be a non-empty object")
        profiles = {
            name: ToolchainConfig.from_dict(name, value, base_dir=config_path.parent)
            for name, value in raw_profiles.items()
        }
        return cls(profiles, default=str(payload.get("default") or next(iter(profiles))))

    def resolve(self, name: str | None = None) -> ToolchainConfig:
        selected = self.default if not name or name == "default" else name
        try:
            return self._profiles[selected]
        except KeyError as exc:
            raise KeyError(f"Unknown toolchain profile: {selected}") from exc

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._profiles))


def load_toolchain(
    *, catalog_path: str | Path | None, profile: str,
    orfs_root: str | Path | None = None,
    openroad_bin: str | Path | None = None,
    yosys_bin: str | Path | None = None,
    klayout_bin: str | Path | None = None,
) -> ToolchainConfig:
    if catalog_path:
        return ToolchainCatalog.from_file(catalog_path).resolve(profile).with_overrides(
            orfs_root=orfs_root, openroad_bin=openroad_bin,
            yosys_bin=yosys_bin, klayout_bin=klayout_bin,
        )
    return ToolchainConfig.from_environment(
        name=profile, orfs_root=orfs_root, openroad_bin=openroad_bin,
        yosys_bin=yosys_bin, klayout_bin=klayout_bin,
    )
