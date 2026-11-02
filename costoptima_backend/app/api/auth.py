from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, status, Response, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session as DbSession
from app.database import get_db
from app.models import User, LoginSession as SessionModel, RolePermission, Permission, Role
from app.schemas import UserOut, MeOut, PermissionOut, AuthOut
from app.security import (verify_login_password, generate_session_token, current_user as get_current_user,
    COOKIE_NAME as SESSION_COOKIE_NAME, check_login_limit, csrf_token, SESSION_PATTERN)
from app.config import get_settings
from app.audit import context as audit_context, session_ref as audit_session_ref, record as audit_record, record_independent
router = APIRouter(prefix="/api/auth", tags=["Auth"])
def _create_session(db: DbSession, user: User) -> str:
    token = generate_session_token()
    audit_context().update(actor_user_id=user.id, session_ref=audit_session_ref(token))
    route = audit_context().get("route", "")
    audit_record(db, "auth", "sso.login" if "sso" in route else "login", outcome="success", target_type="users", target_id=user.id)
    audit_context()["sso_recorded"] = "sso" in route
    expires_at = datetime.utcnow() + timedelta(minutes=(get_settings().session_hours * 60))
    session = SessionModel(id=token, user_id=user.id, expires_at=expires_at)
    session.created_by = user.id
    session.modified_by = user.id
    db.add(session)
    db.commit()
    return token


def _permissions_for(db: DbSession, user: User):
    if not user.role_id:
        return []
    rows = (
        db.query(Permission)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .join(Role, Role.id == RolePermission.role_id)
        .filter(Role.is_deleted.is_(False), RolePermission.role_id == user.role_id, RolePermission.is_deleted.is_(False),
                Permission.is_deleted.is_(False))
        .all()
    )
    return [PermissionOut.model_validate(p) for p in rows]


def _set_session_cookie(response: Response, token: str, max_age_seconds: int):
    response.set_cookie(
        key=SESSION_COOKIE_NAME, value=token, httponly=True,
        secure=get_settings().cookie_secure, samesite="lax",
        max_age=max_age_seconds, path="/api",
    )


@router.post("/login", response_model=AuthOut)
def login(response: Response, request: Request, form_data: OAuth2PasswordRequestForm = Depends(), db: DbSession = Depends(get_db)):
    check_login_limit(request, form_data.username)
    user = db.query(User).filter(User.email == form_data.username.strip().lower(), User.is_deleted.is_(False)).first()
    if not verify_login_password(form_data.password, user) or not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    if user.status != "Active":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    user = db.query(User).filter(User.id == user.id).populate_existing().with_for_update().one()
    if user.is_deleted or user.status != "Active":
        raise HTTPException(401, "Invalid email or password")
    token = _create_session(db, user)
    _set_session_cookie(response, token, (get_settings().session_hours * 60) * 60)
    return AuthOut(**UserOut.model_validate(user).model_dump(), permissions=_permissions_for(db, user), csrf_token=csrf_token(token))


@router.post("/logout")
def logout(request: Request, response: Response, db: DbSession = Depends(get_db)):
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token and SESSION_PATTERN.fullmatch(token):
        import secrets
        if not secrets.compare_digest(request.headers.get("x-csrf-token", "").encode(), csrf_token(token).encode()):
            raise HTTPException(403, "Invalid CSRF token")
        session = db.query(SessionModel).filter(SessionModel.id == token).first()
        if session and session.id == token:
            try:
                audit_context().update(actor_user_id=session.user_id, session_ref=audit_session_ref(token))
                session.is_deleted = True
                audit_record(db, "auth", "logout", outcome="success")
                db.commit()
            except Exception:
                # Revocation must succeed even if the ledger cannot be persisted.
                db.rollback()
                db.info["audit_skip"] = True
                session = db.get(SessionModel, token)
                if session:
                    session.is_deleted = True
                    db.commit()
                try:
                    record_independent("auth", "logout", outcome="success", metadata={"audit_degraded":True})
                except Exception:
                    # Revocation also takes priority if integrity material becomes unavailable.
                    from app.observability import log_audit_fallback
                    log_audit_fallback({"resource":"auth", "action":"logout", "outcome":"success",
                        "request_id":audit_context().get("request_id"), "integrity":"unavailable"})
    response.delete_cookie(SESSION_COOKIE_NAME, path="/api")
    return {"message": "Logged out"}


@router.get("/me", response_model=AuthOut)
def me(request: Request, current_user: User = Depends(get_current_user), db: DbSession = Depends(get_db)):
    return AuthOut(**UserOut.model_validate(current_user).model_dump(), permissions=_permissions_for(db, current_user), csrf_token=csrf_token(request.cookies[SESSION_COOKIE_NAME]))


# Adapted original SSO routes use the existing local session/cookie functions.
import secrets
import math
import hashlib
import hmac
import importlib.util
import json
import time
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl
from urllib.error import HTTPError, URLError
from urllib.request import Request as UrlRequest, urlopen
from fastapi.responses import RedirectResponse

SSO_COOKIE = "costoptima_sso_state"
TOKEN_KEYS = ("jwt_token", "token", "jwt", "id_token", "access_token")


def _sso_readiness():
    settings = get_settings()
    if not settings.sso_enabled or not settings.sso_login_url:
        return False, "Corporate SSO is not configured. Local sign-in remains available."
    if urlsplit(settings.sso_login_url).scheme != "https":
        return False, "The configured SSO login URL must use HTTPS."
    if settings.sso_token_info_url:
        if urlsplit(settings.sso_token_info_url).scheme == "https":
            return True, ""
        return False, "The SSO token-validation endpoint must use HTTPS."
    if (settings.sso_jwt_verify and (settings.sso_jwt_public_key or settings.sso_jwt_secret)
            and settings.sso_jwt_issuer and settings.sso_jwt_audience and importlib.util.find_spec("jwt")):
        return True, ""
    return False, "Corporate SSO needs a trusted token-validation endpoint or verified JWT configuration. The legacy AES-only/unverified-token path is disabled."


def _frontend_sso_callback(**params):
    origin = get_settings().frontend_origin.rstrip("/")
    if origin not in get_settings().origins:
        raise HTTPException(503, "FRONTEND_ORIGIN must match a trusted application origin")
    return origin + "/sso/callback" + ("?" + urlencode(params) if params else "")


def _raw_query_token(request: Request) -> str | None:
    """Original extraction, preserving literal '+' and omitting token logging."""
    preserved = getattr(request.state, "sso_query", None)
    if preserved is not None:
        return next((preserved[k] for k in TOKEN_KEYS if preserved.get(k)), None)
    raw_query = request.scope.get("query_string", b"")
    raw_query_text = raw_query.decode("utf-8", errors="replace") if isinstance(raw_query, bytes) else str(raw_query)
    for pair in raw_query_text.split("&"):
        key, separator, value = pair.partition("=")
        if separator and unquote(key) in TOKEN_KEYS:
            return unquote(value)
    return None


def _sso_token_from_request(request, form=None):
    values = form or {}
    return next((values[k] for k in TOKEN_KEYS if values.get(k)), None) or _raw_query_token(request)


def _decode_sso_jwt(jwt_token):
    settings = get_settings()
    if not (settings.sso_jwt_verify and settings.sso_jwt_issuer and settings.sso_jwt_audience):
        raise HTTPException(503, "Verified JWT configuration is required")
    try:
        import jwt
    except ImportError:
        raise HTTPException(503, "PyJWT is required for signed JWT SSO")
    key = settings.sso_jwt_public_key or settings.sso_jwt_secret
    algorithms = [a.strip() for a in settings.sso_jwt_algorithms.split(',') if a.strip()]
    if not key or not algorithms or any(a not in {"RS256", "RS384", "RS512", "ES256", "ES384", "ES512", "HS256", "HS384", "HS512"} for a in algorithms):
        raise HTTPException(503, "Invalid signed JWT configuration")
    if any(a.startswith('HS') for a in algorithms) and any(not a.startswith('HS') for a in algorithms):
        raise HTTPException(503, "Do not mix symmetric and asymmetric JWT algorithms")
    try:
        return jwt.decode(jwt_token, key, algorithms=algorithms, audience=settings.sso_jwt_audience,
            issuer=settings.sso_jwt_issuer, options={"require": ["exp", "iat", "iss", "aud"]})
    except jwt.PyJWTError:
        raise HTTPException(401, "SSO token validation failed")
def _claim(payload: dict, names: str) -> str | None:
    for name in [x.strip() for x in names.split(',') if x.strip()]:
        value = payload.get(name)
        if value:
            if isinstance(value, dict):
                for nested_name in ("employee_id", "employeeId", "emp_id", "empId", "email", "mail", "user_mail", "emailaddress"):
                    nested_value = value.get(nested_name)
                    if nested_value:
                        return str(nested_value).strip()
            return str(value).strip()
    return None


def _employee_id_from_payload(payload: dict) -> str | None:
    employee_id = _claim(payload, get_settings().sso_employee_id_claims)
    if employee_id:
        return employee_id
    subject = str(payload.get("sub") or "").strip()
    if "_" in subject:
        prefix = subject.split("_", 1)[0].strip()
        if prefix:
            return prefix
    return None


def _fetch_opaque_sso_payload(token: str) -> dict:

    if not get_settings().sso_token_info_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "CC Services returned an opaque SSO token, not a plain JWT. "
                "Configure a trusted HTTPS SSO_TOKEN_INFO_URL."
            ),
        )

    method = (get_settings().sso_token_info_method or "POST").upper()
    field = get_settings().sso_token_info_field or "jwt_token"
    headers = {"Accept": "application/json"}
    url = get_settings().sso_token_info_url
    body = None
    if get_settings().sso_token_info_header:
        headers[get_settings().sso_token_info_header] = token
    elif method == "GET":
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}{urlencode({field: token})}"
    else:
        headers["Content-Type"] = "application/json"
        body = json.dumps({field: token}).encode("utf-8")

    request = UrlRequest(url, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=15) as response:
            data = response.read().decode("utf-8")
    except HTTPError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"SSO token validation failed: HTTP {exc.code}") from exc
    except URLError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="SSO token validation service is unavailable") from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="SSO token validation failed") from exc

    try:
        payload = json.loads(data)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="SSO token validation did not return JSON") from exc
    if isinstance(payload, dict):
        for key in ("data", "user", "profile", "claims"):
            if isinstance(payload.get(key), dict):
                merged = dict(payload[key])
                merged.update({k: v for k, v in payload.items() if k not in {"data", "user", "profile", "claims"}})
                return merged
        return payload
    raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="SSO token validation returned an unsupported response")


# ORIGINAL TOKEN COMPATIBILITY ONLY: these functions parse/decrypt untrusted data.
# Their dictionaries MUST NOT be used for user lookup or session creation.
# Only _decode_sso_token below returns an authenticated identity.
import base64
from urllib.parse import parse_qs, unquote

def _decode_jwt_payload_unverified(jwt_token: str) -> dict:
    try:
        parts = jwt_token.split('.')
        if len(parts) < 2:
            raise ValueError('Invalid JWT format')
        payload = parts[1]
        payload += '=' * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload.encode('utf-8')).decode('utf-8'))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f'Invalid SSO token: {exc}')

def _looks_like_jwt(token: str) -> bool:
    return len(token.split('.')) == 3

def _payload_from_decrypted_text(text: str) -> dict:
    value = (text or '').strip()
    if not value:
        raise ValueError('decrypted token is empty')
    if _looks_like_jwt(value):
        return _decode_jwt_payload_unverified(value)
    try:
        payload = json.loads(value)
        if isinstance(payload, dict):
            return payload
    except Exception:
        pass
    if '=' in value and ('&' in value or value.split('=', 1)[0] in {'employee_id', 'email', 'mail', 'emp_id'}):
        parsed = parse_qs(value, keep_blank_values=True)
        return {key: values[-1] for key, values in parsed.items() if values}
    return {'employee_id': value}

def _looks_like_base64_text(value: bytes) -> bool:
    text = value.strip()
    if not text:
        return False
    allowed = b'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=_-\r\n'
    return all((char in allowed for char in text))

def _decode_base64_candidate(value: str | bytes, label: str) -> bytes:
    if isinstance(value, bytes):
        text = value.decode('utf-8', errors='strict').strip()
    else:
        text = str(value or '').strip()
    text += '=' * ((4 - len(text) % 4) % 4)
    try:
        return base64.b64decode(text, validate=True)
    except Exception as standard_exc:
        return base64.urlsafe_b64decode(text)

def _decrypt_aes_ecb(encrypted: bytes, key: bytes, label: str) -> str:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives import padding
    if len(encrypted) % 16 != 0:
        raise ValueError(f'encrypted byte length {len(encrypted)} is not a multiple of AES block size 16')
    cipher = Cipher(algorithms.AES(key), modes.ECB())
    decryptor = cipher.decryptor()
    padded = decryptor.update(encrypted) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    plain = unpadder.update(padded) + unpadder.finalize()
    return plain.decode('utf-8')

def _decrypt_cc_services_text_compat(token: str) -> str | None:
    secret = get_settings().sso_token_decrypt_secret
    if not secret:
        return None
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives import padding
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="cryptography is required for SSO token decrypt") from exc
    key = secret.encode('utf-8')
    if len(key) not in {16, 24, 32}:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='SSO_TOKEN_DECRYPT_SECRET must be 16, 24, or 32 characters for AES')
    raw_token = str(token or '').strip()
    candidates = [('raw', raw_token), ('remove_whitespace', ''.join(raw_token.split())), ('space_to_plus', raw_token.replace(' ', '+')), ('space_to_plus_remove_whitespace', ''.join(raw_token.replace(' ', '+').split()))]
    if raw_token.startswith('+') or raw_token[:8].count('+') >= 3:
        candidates.append(('remove_inserted_plus', raw_token.replace('+', '')))
    last_error: Exception | None = None
    for label, candidate in candidates:
        normalized_token = candidate.strip()
        if not normalized_token:
            continue
        padding_needed = (4 - len(normalized_token) % 4) % 4
        if padding_needed:
            normalized_token = f"{normalized_token}{'=' * padding_needed}"
        try:
            encrypted = _decode_base64_candidate(normalized_token, label)
            if len(encrypted) % 16 != 0 and _looks_like_base64_text(encrypted):
                nested_encrypted = _decode_base64_candidate(encrypted, f'{label}:nested')
                plain_text = _decrypt_aes_ecb(nested_encrypted, key, f'{label}:nested')
            else:
                plain_text = _decrypt_aes_ecb(encrypted, key, label)
            return plain_text
        except Exception as exc:
            last_error = exc
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Could not decrypt CC Services SSO token") from last_error


def _decrypt_cc_services_token(token: str) -> dict | None:
    """Original payload compatibility; decrypted claims are NOT authenticated."""
    text = _decrypt_cc_services_text_compat(token)
    return _payload_from_decrypted_text(text) if text is not None else None


def _decode_sso_token(token):
    ready, reason = _sso_readiness()
    if not ready:
        raise HTTPException(503, reason)
    if get_settings().sso_token_info_url:
        payload = _fetch_opaque_sso_payload(token)
    elif _looks_like_jwt(token):
        payload = _decode_sso_jwt(token)
    else:
        # AES is transport compatibility, never identity verification.
        inner_token = _decrypt_cc_services_text_compat(token)
        if not inner_token:
            raise HTTPException(503, "SSO_TOKEN_DECRYPT_SECRET is required for encrypted signed JWTs")
        if not _looks_like_jwt(inner_token.strip()):
            raise HTTPException(401, "Decrypted SSO claims require authenticated provider validation")
        payload = _decode_sso_jwt(inner_token.strip())
    if not isinstance(payload, dict) or payload.get("active") is False:
        raise HTTPException(401, "SSO identity validation failed")
    if "exp" in payload:
        try:
            if not math.isfinite(float(payload["exp"])) or float(payload["exp"]) <= time.time():
                raise HTTPException(401, "SSO token has expired")
        except (TypeError, ValueError):
            raise HTTPException(401, "Invalid SSO expiration")
    return payload


def _sso_user_from_payload(db, payload):
    # Original employee-ID-first, email-second mapping. No automatic profile writes or provisioning.
    email = _claim(payload, get_settings().sso_email_claims)
    employee_id = _employee_id_from_payload(payload)
    if not email and not employee_id:
        raise HTTPException(401, "SSO token has no mapped identity")
    user = None
    if employee_id:
        user = db.query(User).filter(User.employee_id == employee_id.strip(), User.is_deleted.is_(False)).first()
    if not user and email:
        user = db.query(User).filter(User.email == email.lower(), User.is_deleted.is_(False)).first()
    if not user or user.status != "Active":
        raise HTTPException(403, "No active Cost Pilot account is mapped to this SSO identity")
    if email:
        other = db.query(User).filter(User.email == email.lower(), User.is_deleted.is_(False)).first()
        if other and other.id != user.id:
            raise HTTPException(403, "SSO identity mapping is inconsistent")
    return user


def _state_value(nonce, timestamp):
    text = nonce + "." + str(timestamp)
    signature = hmac.new(get_settings().csrf_signing_key.encode(), text.encode(), hashlib.sha256).hexdigest()
    return text + "." + signature


def _verify_sso_state(request, state):
    value = request.cookies.get(SSO_COOKIE, "")
    try:
        nonce, timestamp, signature = value.split('.')
        valid = _state_value(nonce, int(timestamp))
        if not secrets.compare_digest(value.encode(), valid.encode()) or not secrets.compare_digest(nonce.encode(), str(state or '').encode()) or not 0 <= time.time() - int(timestamp) <= 600:
            raise ValueError()
    except (ValueError, TypeError):
        raise HTTPException(403, "SSO sign-in state is missing or expired. Start sign-in again.")


@router.get("/sso/status")
def sso_status():
    ready, reason = _sso_readiness()
    return {"enabled": bool(get_settings().sso_enabled and get_settings().sso_login_url), "ready": ready, "message": reason}


@router.get("/sso/login")
def sso_login(request: Request, db: DbSession = Depends(get_db)):
    # Original providers may return the token to the login route itself.
    token = _sso_token_from_request(request)
    if token:
        query = getattr(request.state, "sso_query", dict(request.query_params))
        try:
            response, _, _ = _finish_sso_login(db, token, request, query.get("state"), redirect=True)
            return response
        except HTTPException as error:
            record_independent("auth", "sso.login", outcome="failure", error_category="http_"+str(error.status_code))
            audit_context()["sso_recorded"] = True
            return RedirectResponse(_frontend_sso_callback(status="error", message=str(error.detail)), status_code=303)
    ready, reason = _sso_readiness()
    if not ready:
        record_independent("auth", "sso.login", outcome="failure", error_category="configuration_unavailable")
        audit_context()["sso_recorded"] = True
        return RedirectResponse(_frontend_sso_callback(status="error", message=reason), status_code=303)
    settings = get_settings()
    nonce = secrets.token_urlsafe(32)
    timestamp = int(time.time())
    callback = settings.sso_callback_url or _frontend_sso_callback()
    parts = urlsplit(callback)
    if (parts.scheme + "://" + parts.netloc) not in settings.origins or parts.path not in {"/sso/callback", "/api/auth/sso/callback", "/sso"}:
        raise HTTPException(503, "SSO callback must use an explicitly trusted application origin and callback route")
    callback = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(dict(parse_qsl(parts.query)) | {"state": nonce}), ""))
    separator = '&' if '?' in settings.sso_login_url else '?'
    response = RedirectResponse(settings.sso_login_url + separator + urlencode({"redirect_uri": callback, "state": nonce}), status_code=303)
    response.set_cookie(SSO_COOKIE, _state_value(nonce, timestamp), httponly=True, secure=settings.cookie_secure, samesite="lax", max_age=600, path="/")
    return response


def _finish_sso_login(db, token, request, state, *, redirect=False):
    _verify_sso_state(request, state)
    payload = _decode_sso_token(str(token))
    user = _sso_user_from_payload(db, payload)
    user = db.query(User).filter(User.id == user.id).populate_existing().with_for_update().one()
    if user.is_deleted or user.status != "Active":
        raise HTTPException(403, "This account is inactive")
    session_token = _create_session(db, user)
    response = RedirectResponse(_frontend_sso_callback(status="success"), status_code=303) if redirect else Response()
    _set_session_cookie(response, session_token, get_settings().session_hours * 3600)
    response.delete_cookie(SSO_COOKIE, path="/")
    return response, user, session_token


@router.api_route("/sso/callback", methods=["GET", "POST"])
async def sso_callback(request: Request, db: DbSession = Depends(get_db)):
    form = dict(await request.form()) if request.method == "POST" else {}
    query = getattr(request.state, "sso_query", dict(request.query_params))
    token = _sso_token_from_request(request, form)
    try:
        if not token:
            raise HTTPException(400, "Missing SSO token")
        response, _, _ = _finish_sso_login(db, token, request, form.get("state") or query.get("state"), redirect=True)
        return response
    except HTTPException as error:
        record_independent("auth", "sso.callback", outcome="failure", error_category="http_"+str(error.status_code))
        audit_context()["sso_recorded"] = True
        return RedirectResponse(_frontend_sso_callback(status="error", message=str(error.detail)), status_code=303)


@router.post("/sso/exchange", response_model=AuthOut)
async def sso_exchange(request: Request, response: Response, db: DbSession = Depends(get_db)):
    try:
        data = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(422, "Invalid SSO request")
    if not isinstance(data, dict):
        raise HTTPException(422, "Invalid SSO request")
    token = next((data.get(k) for k in TOKEN_KEYS if data.get(k)), None)
    if not token:
        raise HTTPException(400, "Missing SSO token")
    result, user, session_token = _finish_sso_login(db, token, request, data.get("state"))
    for name, value in result.raw_headers:
        if name.lower() == b"set-cookie":
            response.raw_headers.append((name, value))
    return AuthOut(**UserOut.model_validate(user).model_dump(), permissions=_permissions_for(db, user), csrf_token=csrf_token(session_token))
