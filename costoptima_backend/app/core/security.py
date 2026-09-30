import secrets
from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def generate_session_token() -> str:
    """Opaque, unguessable session id used as both the sessions.id primary key and the
    session-cookie value. Not a JWT: meaningless without the matching server-side row."""
    return secrets.token_urlsafe(48)
