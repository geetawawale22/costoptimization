import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    backend_env = Path(__file__).resolve().parents[1] / ".env"
    load_dotenv(backend_env)
except Exception:
    pass


class Settings:
    SECRET_KEY: str = os.getenv("SECRET_KEY", "change-this-secret-in-production")
    SESSION_EXPIRE_MINUTES: int = int(os.getenv("SESSION_EXPIRE_MINUTES", 60 * 8))  # 8 hours
    SESSION_COOKIE_SECURE: bool = os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true"
    FRONTEND_ORIGIN: str = os.getenv("FRONTEND_ORIGIN", "http://172.26.104.156:5173")
    UPLOAD_DIR: str = os.getenv("UPLOAD_DIR", "./uploads")
    LOG_DIR: str = os.getenv("LOG_DIR", "./logs")
    LOG_FILE: str = os.getenv("LOG_FILE", "costoptima.log")
    PCM_VERBOSE_LOGS: bool = os.getenv("PCM_VERBOSE_LOGS", "true").lower() == "true"

    # SSO is optional. Keep normal email/password login available while these are
    # being configured for the client's identity provider.
    SSO_ENABLED: bool = os.getenv("SSO_ENABLED", "true").lower() == "true"
    SSO_LOGIN_URL: str = os.getenv(
        "SSO_LOGIN_URL",
        "https://ccservices.mahindra.com/auth/login?id=afa93cde-c376-40dc-91fc-5bde44fad392&env=DEV",
    )
    SSO_CALLBACK_URL: str = os.getenv("SSO_CALLBACK_URL", "")
    SSO_JWT_VERIFY: bool = os.getenv("SSO_JWT_VERIFY", "false").lower() == "true"
    SSO_JWT_SECRET: str = os.getenv("SSO_JWT_SECRET", "")
    SSO_JWT_PUBLIC_KEY: str = os.getenv("SSO_JWT_PUBLIC_KEY", "")
    SSO_JWT_ALGORITHMS: str = os.getenv("SSO_JWT_ALGORITHMS", "HS256,RS256")
    SSO_TOKEN_INFO_URL: str = os.getenv("SSO_TOKEN_INFO_URL", "")
    SSO_TOKEN_INFO_METHOD: str = os.getenv("SSO_TOKEN_INFO_METHOD", "POST")
    SSO_TOKEN_INFO_FIELD: str = os.getenv("SSO_TOKEN_INFO_FIELD", "jwt_token")
    SSO_TOKEN_INFO_HEADER: str = os.getenv("SSO_TOKEN_INFO_HEADER", "")
    SSO_TOKEN_DECRYPT_SECRET: str = os.getenv("SSO_TOKEN_DECRYPT_SECRET", "")
    SSO_AUTO_CREATE_USERS: bool = os.getenv("SSO_AUTO_CREATE_USERS", "false").lower() == "true"
    SSO_DEFAULT_ROLE: str = os.getenv("SSO_DEFAULT_ROLE", "Viewer")
    SSO_EMAIL_CLAIMS: str = os.getenv("SSO_EMAIL_CLAIMS", "email,mail,user_mail,emailaddress,preferred_username,upn,unique_name")
    SSO_EMPLOYEE_ID_CLAIMS: str = os.getenv("SSO_EMPLOYEE_ID_CLAIMS", "employee_id,employeeId,emp_id,empId,uid,user")
    SSO_NAME_CLAIMS: str = os.getenv("SSO_NAME_CLAIMS", "name,display_name,given_name")


settings = Settings()
os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
os.makedirs(settings.LOG_DIR, exist_ok=True)
