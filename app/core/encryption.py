from __future__ import annotations

from cryptography.fernet import Fernet

from app.core.config import settings


def get_fernet() -> Fernet:
    if not settings.ENCRYPTION_KEY:
        raise ValueError(
            "ENCRYPTION_KEY is not configured. "
            "Generate one with: python -c "
            '"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
        )
    return Fernet(settings.ENCRYPTION_KEY.encode())


def encrypt_value(value: str) -> str:
    return get_fernet().encrypt(value.encode()).decode()


def decrypt_value(encrypted_value: str) -> str:
    return get_fernet().decrypt(encrypted_value.encode()).decode()
