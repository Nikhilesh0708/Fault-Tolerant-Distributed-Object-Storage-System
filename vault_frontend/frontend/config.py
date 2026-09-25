"""Frontend settings. Separate object storage from Member 3's recovery API."""
import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit


def validate_url(value: str) -> str:
    value = value.strip().rstrip("/")
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("Enter a complete http:// or https:// address.")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("API addresses cannot contain credentials, queries, or fragments.")
    try:
        parts.port
    except ValueError as exc:
        raise ValueError("Enter a valid port number.") from exc
    return value


@dataclass(frozen=True)
class Config:
    storage_url: str = "http://127.0.0.1:8000"
    recovery_url: str = "http://127.0.0.1:8003"
    public_storage_url: str = "http://127.0.0.1:8000"
    storage_token: str = field(default="", repr=False)
    recovery_token: str = field(default="", repr=False)
    upload_limit_mb: int = 32
    small_download_limit_mb: int = 8
    chunk_size: int = 65536
    metadata_limit_bytes: int = 2 * 1024**2
    page_size: int = 100
    connect_timeout: float = 2.0
    read_timeout: float = 15.0
    upload_timeout: float = 120.0
    metadata_ttl: float = 5.0

    def __post_init__(self):
        for name in ("storage_url", "recovery_url", "public_storage_url"):
            validate_url(getattr(self, name))
        if not 1 <= self.upload_limit_mb <= 32:
            raise ValueError("The frontend upload limit must be between 1 and 32 MiB.")

    @classmethod
    def from_env(cls):
        base = os.getenv("VAULT_STORAGE_URL", "http://127.0.0.1:8000")
        return cls(storage_url=validate_url(base),
                   recovery_url=validate_url(os.getenv("VAULT_RECOVERY_URL", "http://127.0.0.1:8003")),
                   public_storage_url=validate_url(os.getenv("VAULT_PUBLIC_STORAGE_URL", base)),
                   storage_token=os.getenv("VAULT_STORAGE_TOKEN", ""),
                   recovery_token=os.getenv("VAULT_RECOVERY_TOKEN", ""),
                   upload_limit_mb=int(os.getenv("VAULT_UPLOAD_LIMIT_MB", "32")))
