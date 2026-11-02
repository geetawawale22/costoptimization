from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit
from ipaddress import ip_address
from pathlib import Path
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    app_env: Literal["development", "test", "staging", "production"] = "production"
    calculation_development_enabled: bool = False
    audit_signing_key: str = ""
    audit_key_id: str = "v1"
    audit_log_dir: str = str(Path(__file__).resolve().parents[1] / "logs")
    app_config_encryption_key: str = ""
    sso_enabled: bool = False
    sso_login_url: str = ""
    sso_callback_url: str = ""
    sso_jwt_verify: bool = True
    sso_jwt_secret: str = ""
    sso_jwt_public_key: str = ""
    sso_jwt_algorithms: str = "RS256"
    sso_jwt_issuer: str = ""
    sso_jwt_audience: str = ""
    # Original AES compatibility; decryption does not establish identity.
    sso_token_decrypt_secret: str = ""
    sso_token_info_url: str = ""
    sso_token_info_method: str = "POST"
    sso_token_info_field: str = "jwt_token"
    sso_token_info_header: str = ""
    sso_email_claims: str = "email,mail,user_mail,emailaddress,preferred_username,upn,unique_name"
    sso_employee_id_claims: str = "employee_id,employeeId,emp_id,empId,uid,user"
    sso_name_claims: str = "name,display_name,given_name"
    frontend_origin: str = ""
    database_url: str
    csrf_signing_key: str = Field(min_length=64)
    trusted_origins: str = "http://127.0.0.1:5187,http://localhost:5187"
    cookie_secure: bool = False
    session_hours: int = Field(default=8, ge=1, le=24)
    model_config = SettingsConfigDict(env_file=Path(__file__).resolve().parents[1] / ".env", extra="ignore")

    @field_validator("database_url")
    @classmethod
    def validate_database(cls, value):
        url = make_url(value)
        if url.drivername != "mysql+pymysql" or url.database != "costoptima":
            raise ValueError("Use the approved mysql+pymysql connection to costoptima")
        return value

    @property
    def development_environment_verified(self):
        """Explicit local development designation; never infer it from a flag alone."""
        def local_host(value):
            if value == "localhost": return True
            try: return ip_address(value or "").is_loopback
            except ValueError: return False
        origins = self.origins
        return (self.app_env in {"development", "test"}
                and local_host(make_url(self.database_url).host)
                and bool(origins) and all(local_host(urlsplit(origin).hostname) for origin in origins)
                and local_host(urlsplit(self.frontend_origin).hostname))

    @property
    def development_calculations_available(self):
        return self.calculation_development_enabled and self.development_environment_verified

    @property
    def origins(self):
        return {value.strip().rstrip("/") for value in self.trusted_origins.split(",") if value.strip()}


@lru_cache
def get_settings():
    return Settings()