from __future__ import annotations

import datetime as dt
import os
import stat
from dataclasses import dataclass
from pathlib import Path


class InvalidDirectedResearchPackageError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class FixedDirectedResearchPackage:
    root: Path

    @classmethod
    def from_source_root(cls, source_root: Path) -> FixedDirectedResearchPackage:
        root = source_root / "directed-research"
        require_private_directory(root, root)
        return cls(root=root)

    def hypothesis_manifest(self) -> Path:
        path = self.root / "hypothesis.json"
        require_private_file(path, self.root)
        return path

    def experiment_spec(self) -> Path:
        path = self.root / "experiment.json"
        require_private_file(path, self.root)
        return path

    def entitlement_contract(self) -> Path:
        path = self.root / "entitlement.json"
        require_private_file(path, self.root)
        return path

    def session_directories(self, dates: tuple[dt.date, ...]) -> tuple[Path, ...]:
        sessions_root = self.root / "sessions"
        require_private_directory(sessions_root, self.root)
        paths = tuple(sessions_root / str(item) for item in dates)
        for path in paths:
            require_private_tree(path, self.root)
        return paths


def require_private_tree(path: Path, authority_root: Path) -> None:
    require_private_directory(path, authority_root)
    for descendant in path.rglob("*"):
        metadata = descendant.lstat()
        if stat.S_ISDIR(metadata.st_mode):
            require_private_directory(descendant, authority_root)
        else:
            require_private_file(descendant, authority_root)


def require_private_directory(path: Path, authority_root: Path) -> None:
    metadata = _metadata_within_authority(path, authority_root)
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o700:
        raise InvalidDirectedResearchPackageError("directed_private_directory_invalid")


def ensure_private_directory(path: Path, authority_root: Path) -> None:
    if not path.exists() and not path.is_symlink():
        try:
            os.mkdir(path, mode=0o700)
        except OSError as error:
            raise InvalidDirectedResearchPackageError("directed_private_directory_create_failed") from error
    require_private_directory(path, authority_root)


def require_private_file(path: Path, authority_root: Path) -> None:
    metadata = _metadata_within_authority(path, authority_root)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise InvalidDirectedResearchPackageError("directed_private_file_invalid")


def _metadata_within_authority(path: Path, authority_root: Path) -> os.stat_result:
    try:
        absolute_root = authority_root.absolute()
        absolute_path = path.absolute()
        if not absolute_path.is_relative_to(absolute_root):
            raise InvalidDirectedResearchPackageError("directed_package_escape")
        if absolute_root.resolve(strict=True) != absolute_root:
            raise InvalidDirectedResearchPackageError("directed_package_root_alias")
        if absolute_path.resolve(strict=True) != absolute_path:
            raise InvalidDirectedResearchPackageError("directed_package_path_alias")
        metadata = path.lstat()
    except OSError as error:
        raise InvalidDirectedResearchPackageError("directed_package_identity_unavailable") from error
    if stat.S_ISLNK(metadata.st_mode):
        raise InvalidDirectedResearchPackageError("directed_package_symlink")
    return metadata


__all__ = (
    "FixedDirectedResearchPackage",
    "InvalidDirectedResearchPackageError",
    "ensure_private_directory",
    "require_private_file",
)
