from datetime import datetime, timedelta
import base64
import json
import secrets
from urllib.parse import parse_qs, unquote
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request as UrlRequest, urlopen
from fastapi import APIRouter, Depends, HTTPException, status, Response, Request
from fastapi.responses import RedirectResponse
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session as DbSession

from app.database import get_db
from app.models.user import User, Session as SessionModel, Role, RolePermission, Permission
from app.schemas.user import UserOut, MeOut, PermissionOut
from app.core.security import verify_password, generate_session_token, hash_password
from app.core.deps import get_current_user, SESSION_COOKIE_NAME
from app.config import settings

router = APIRouter(prefix="/auth", tags=["Auth"])
sso_redirect_router = APIRouter(tags=["Auth"])


def _create_session(db: DbSession, user: User) -> str:
    token = generate_session_token()
    expires_at = datetime.utcnow() + timedelta(minutes=settings.SESSION_EXPIRE_MINUTES)
    session = SessionModel(id=token, user_id=user.id, expires_at=expires_at)
    session.created_by = user.id
    session.modified_by = user.id
    db.add(session)
    db.commit()
    return token


def _frontend_sso_callback(**params) -> str:
    clean = {k: v for k, v in params.items() if v is not None and v != ""}
    suffix = f"?{urlencode(clean)}" if clean else ""
    return f"{settings.FRONTEND_ORIGIN.rstrip('/')}/sso/callback{suffix}"


def _sso_token_from_request(request: Request, form: dict | None = None) -> str | None:
    form = form or {}
    query_token = _raw_query_token(request)
    return (
        form.get("jwt_token") or form.get("token") or form.get("jwt")
        or form.get("id_token") or form.get("access_token")
        or query_token
        or request.query_params.get("jwt_token") or request.query_params.get("token")
        or request.query_params.get("jwt") or request.query_params.get("id_token")
        or request.query_params.get("access_token")
    )


def _raw_query_token(request: Request) -> str | None:
    raw_query = request.scope.get("query_string", b"")
    if isinstance(raw_query, bytes):
        raw_query_text = raw_query.decode("utf-8", errors="replace")
    else:
        raw_query_text = str(raw_query)
    if not raw_query_text:
        return None
    for pair in raw_query_text.split("&"):
        key, separator, value = pair.partition("=")
        if not separator:
            continue
        if unquote(key) in {"jwt_token", "token", "jwt", "id_token", "access_token"}:
            print("SSO raw query token used; plus signs preserved:", "+" in value)
            return unquote(value)
    return None


@sso_redirect_router.api_route("/sso", methods=["GET", "POST"])
async def sso_frontend_callback_alias(request: Request, db: DbSession = Depends(get_db)):
    """Compatibility callback for CC Services redirects to /sso.

    The configured app callback is the frontend route /sso/callback, but the CC
    service can redirect to this backend root path as /sso?jwt_token=...
    Complete the login here so the browser receives the CostOptima session cookie,
    then redirect to the frontend callback with only a success/error status.
    """
    params = dict(request.query_params)
    form = {}
    if request.method == "POST":
        try:
            form = dict(await request.form())
            params.update(form)
        except Exception:
            pass
    jwt_token = _sso_token_from_request(request, form)
    if not jwt_token:
        return RedirectResponse(_frontend_sso_callback(status="error", message="Missing SSO token"))
    try:
        payload = _decode_sso_token(str(jwt_token))
        user = _sso_user_from_payload(db, payload)
        session_token = _create_session(db, user)
        redirect = RedirectResponse(_frontend_sso_callback(status="success"))
        _set_session_cookie(redirect, session_token, settings.SESSION_EXPIRE_MINUTES * 60)
        return redirect
    except HTTPException as exc:
        return RedirectResponse(_frontend_sso_callback(status="error", message=str(exc.detail)))


def _decode_jwt_payload_unverified(jwt_token: str) -> dict:
    try:
        parts = jwt_token.split('.')
        if len(parts) < 2:
            raise ValueError("Invalid JWT format")
        payload = parts[1]
        payload += '=' * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload.encode('utf-8')).decode('utf-8'))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Invalid SSO token: {exc}")


def _looks_like_jwt(token: str) -> bool:
    return len(token.split(".")) == 3


def _decode_sso_jwt(jwt_token: str) -> dict:
    if settings.SSO_JWT_VERIFY:
        key = settings.SSO_JWT_PUBLIC_KEY or settings.SSO_JWT_SECRET
        if not key:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="SSO JWT verification key is not configured")
        try:
            import jwt  # type: ignore
        except Exception:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="PyJWT is required when SSO_JWT_VERIFY=true")
        algorithms = [x.strip() for x in settings.SSO_JWT_ALGORITHMS.split(',') if x.strip()]
        try:
            return jwt.decode(jwt_token, key, algorithms=algorithms, options={"verify_aud": False})
        except Exception as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"SSO token verification failed: {exc}")
    return _decode_jwt_payload_unverified(jwt_token)


def _fetch_opaque_sso_payload(token: str) -> dict:
    print("===== SSO OPAQUE TOKEN PATH =====")
    print("Token received length:", len(str(token or "")))
    print("Token dot count:", str(token or "").count("."))
    print("Decrypt secret configured:", bool(settings.SSO_TOKEN_DECRYPT_SECRET))
    print("Decrypt secret length:", len(settings.SSO_TOKEN_DECRYPT_SECRET or ""))
    print("Token info URL configured:", bool(settings.SSO_TOKEN_INFO_URL))
    decrypted_payload = _decrypt_cc_services_token(token)
    if decrypted_payload is not None:
        print("SSO decrypt success. Payload keys:", list(decrypted_payload.keys()))
        print("===== END SSO OPAQUE TOKEN PATH =====")
        return decrypted_payload

    if not settings.SSO_TOKEN_INFO_URL:
        print("SSO failed: no decrypt secret loaded and no token info URL configured")
        print("===== END SSO OPAQUE TOKEN PATH =====")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "CC Services returned an opaque SSO token, not a plain JWT. "
                "Configure SSO_TOKEN_DECRYPT_SECRET with the AES key shared by "
                "CC Services, or configure SSO_TOKEN_INFO_URL."
            ),
        )

    method = (settings.SSO_TOKEN_INFO_METHOD or "POST").upper()
    field = settings.SSO_TOKEN_INFO_FIELD or "jwt_token"
    headers = {"Accept": "application/json"}
    url = settings.SSO_TOKEN_INFO_URL
    body = None
    if settings.SSO_TOKEN_INFO_HEADER:
        headers[settings.SSO_TOKEN_INFO_HEADER] = token
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
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"SSO token validation service is unavailable: {exc.reason}") from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"SSO token validation failed: {exc}") from exc

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


def _payload_from_decrypted_text(text: str) -> dict:
    value = (text or "").strip()
    print("SSO decrypted text preview:", value[:120])
    if not value:
        raise ValueError("decrypted token is empty")
    if _looks_like_jwt(value):
        print("SSO decrypted text is an inner JWT; decoding payload")
        return _decode_jwt_payload_unverified(value)
    try:
        payload = json.loads(value)
        if isinstance(payload, dict):
            return payload
    except Exception:
        pass
    if "=" in value and ("&" in value or value.split("=", 1)[0] in {"employee_id", "email", "mail", "emp_id"}):
        parsed = parse_qs(value, keep_blank_values=True)
        return {key: values[-1] for key, values in parsed.items() if values}
    return {"employee_id": value}


def _preview_bytes(value: bytes, limit: int = 96) -> str:
    preview = value[:limit]
    try:
        return preview.decode("utf-8", errors="replace")
    except Exception:
        return repr(preview)


def _looks_like_base64_text(value: bytes) -> bool:
    text = value.strip()
    if not text:
        return False
    allowed = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=_-\r\n"
    return all(char in allowed for char in text)


def _decode_base64_candidate(value: str | bytes, label: str) -> bytes:
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="strict").strip()
    else:
        text = str(value or "").strip()
    text += "=" * ((4 - (len(text) % 4)) % 4)
    print(f"SSO Base64 decode [{label}]: input length:", len(text))
    try:
        return base64.b64decode(text, validate=True)
    except Exception as standard_exc:
        print(f"SSO standard Base64 decode failed [{label}]:", repr(standard_exc))
        return base64.urlsafe_b64decode(text)


def _decrypt_aes_ecb(encrypted: bytes, key: bytes, label: str) -> str:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives import padding

    print(f"SSO AES decrypt [{label}]: encrypted bytes:", len(encrypted))
    print(f"SSO AES decrypt [{label}]: block remainder:", len(encrypted) % 16)
    if len(encrypted) % 16 != 0:
        raise ValueError(f"encrypted byte length {len(encrypted)} is not a multiple of AES block size 16")
    cipher = Cipher(algorithms.AES(key), modes.ECB())
    decryptor = cipher.decryptor()
    padded = decryptor.update(encrypted) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    plain = unpadder.update(padded) + unpadder.finalize()
    return plain.decode("utf-8")


def _decrypt_cc_services_token(token: str) -> dict | None:
    secret = settings.SSO_TOKEN_DECRYPT_SECRET
    if not secret:
        print("SSO decrypt skipped: SSO_TOKEN_DECRYPT_SECRET is empty")
        return None
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives import padding
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"cryptography is required for SSO token decrypt: {exc}") from exc

    key = secret.encode("utf-8")
    if len(key) not in {16, 24, 32}:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="SSO_TOKEN_DECRYPT_SECRET must be 16, 24, or 32 characters for AES")
    raw_token = str(token or "").strip()
    candidates = [
        ("raw", raw_token),
        ("remove_whitespace", "".join(raw_token.split())),
        ("space_to_plus", raw_token.replace(" ", "+")),
        ("space_to_plus_remove_whitespace", "".join(raw_token.replace(" ", "+").split())),
    ]
    if raw_token.startswith("+") or raw_token[:8].count("+") >= 3:
        candidates.append(("remove_inserted_plus", raw_token.replace("+", "")))

    last_error: Exception | None = None
    for label, candidate in candidates:
        normalized_token = candidate.strip()
        if not normalized_token:
            continue
        padding_needed = (4 - (len(normalized_token) % 4)) % 4
        if padding_needed:
            normalized_token = f"{normalized_token}{'=' * padding_needed}"
        print("SSO decrypt candidate:", label)
        print("SSO decrypt attempt: padding added:", padding_needed)
        print("SSO decrypt attempt: normalized token length:", len(normalized_token))
        print("SSO decrypt attempt: token prefix:", normalized_token[:12])
        print("SSO decrypt attempt: token suffix:", normalized_token[-12:])
        try:
            encrypted = _decode_base64_candidate(normalized_token, label)
            print("SSO decrypt attempt: encrypted/decoded bytes:", len(encrypted))
            print("SSO decrypt attempt: decoded byte preview:", _preview_bytes(encrypted))
            if len(encrypted) % 16 != 0 and _looks_like_base64_text(encrypted):
                print("SSO decrypt attempt: decoded bytes look like nested Base64 text; trying second decode")
                nested_encrypted = _decode_base64_candidate(encrypted, f"{label}:nested")
                plain_text = _decrypt_aes_ecb(nested_encrypted, key, f"{label}:nested")
            else:
                plain_text = _decrypt_aes_ecb(encrypted, key, label)
            print("SSO decrypt candidate succeeded:", label)
            return _payload_from_decrypted_text(plain_text)
        except Exception as exc:
            last_error = exc
            print("SSO decrypt candidate failed:", label, repr(exc))

    print("SSO decrypt failed:", repr(last_error))
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Could not decrypt CC Services SSO token: {last_error}") from last_error


def _decode_sso_token(token: str) -> dict:
    print("===== SSO TOKEN DECODE =====")
    print("Token parameter length:", len(str(token or "")))
    print("Token dot count:", str(token or "").count("."))
    print("Looks like JWT:", _looks_like_jwt(str(token or "")))
    if _looks_like_jwt(token):
        print("SSO path selected: JWT decode")
        return _decode_sso_jwt(token)
    print("SSO path selected: opaque/AES decode")
    return _fetch_opaque_sso_payload(token)


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
    employee_id = _claim(payload, settings.SSO_EMPLOYEE_ID_CLAIMS)
    if employee_id:
        return employee_id
    subject = str(payload.get("sub") or "").strip()
    if "_" in subject:
        prefix = subject.split("_", 1)[0].strip()
        if prefix:
            print("SSO employee_id derived from sub prefix:", prefix)
            return prefix
    return None


def _mask(value: str | None) -> str | None:
    if not value:
        return value
    if "@" in value:
        local, domain = value.split("@", 1)
        return f"{local[:2]}***@{domain}"
    return f"{value[:2]}***{value[-2:]}" if len(value) > 4 else "***"


def _first_claim(payload: dict, names: tuple[str, ...]) -> str | None:
    for name in names:
        value = payload.get(name)
        if value:
            return str(value).strip()
    return None


def _sso_profile_updates(payload: dict) -> dict:
    given = _first_claim(payload, ("givenname", "given_name", "first_name"))
    surname = _first_claim(payload, ("surname", "family_name", "last_name"))
    composed_name = " ".join(part for part in (given, surname) if part).strip()
    name = (
        _first_claim(payload, ("displayname", "display_name", "name", "full_name"))
        or composed_name
    )
    return {
        "name": name,
        "job_title": _first_claim(payload, ("job_title", "jobTitle", "title", "designation", "position")),
        "department": _first_claim(payload, ("department", "dept")),
        "company": _first_claim(payload, ("company", "organization", "org")),
        "mobile": _first_claim(payload, ("mobile", "phone_number", "phone", "telephone")),
    }


def _apply_sso_profile_updates(db: DbSession, user: User, payload: dict, employee_id: str | None, email: str | None) -> User:
    updates = _sso_profile_updates(payload)
    if employee_id and not user.employee_id:
        updates["employee_id"] = employee_id
    changed = []
    for field, value in updates.items():
        if value and getattr(user, field, None) != value:
            setattr(user, field, value)
            changed.append(field)
    if email and user.email != email and not db.query(User).filter(User.email == email, User.id != user.id, User.is_deleted.is_(False)).first():
        user.email = email
        changed.append("email")
    if changed:
        print("SSO profile fields updated:", changed)
        db.commit()
        db.refresh(user)
    return user


def _sso_user_from_payload(db: DbSession, payload: dict) -> User:
    email = _claim(payload, settings.SSO_EMAIL_CLAIMS)
    employee_id = _employee_id_from_payload(payload)
    print("SSO user lookup email:", _mask(email))
    print("SSO user lookup employee_id:", _mask(employee_id))
    if not email and not employee_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="SSO token does not contain an email or employee_id claim")
    email = email.lower() if email else None
    employee_id = employee_id.strip() if employee_id else None
    name = _claim(payload, settings.SSO_NAME_CLAIMS) or email or employee_id
    user = None
    if employee_id:
        user = db.query(User).filter(User.employee_id == employee_id, User.is_deleted.is_(False)).first()
        print("SSO employee_id user match:", bool(user))
    if not user and email:
        user = db.query(User).filter(User.email == email, User.is_deleted.is_(False)).first()
        print("SSO email user match:", bool(user))
    if user:
        if user.status != "Active":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This account is inactive")
        return _apply_sso_profile_updates(db, user, payload, employee_id, email)
    if not settings.SSO_AUTO_CREATE_USERS:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No CostOptima user is mapped to this SSO account")
    role = db.query(Role).filter(Role.name == settings.SSO_DEFAULT_ROLE, Role.is_deleted.is_(False)).first()
    if not role:
        role = db.query(Role).filter(Role.is_deleted.is_(False)).order_by(Role.id).first()
    user = User(
        name=name,
        employee_id=employee_id,
        email=email or f"{employee_id}@sso.local",
        hashed_password=hash_password(secrets.token_urlsafe(32)),
        job_title=_sso_profile_updates(payload).get("job_title"),
        department=_sso_profile_updates(payload).get("department"),
        company=_sso_profile_updates(payload).get("company"),
        mobile=_sso_profile_updates(payload).get("mobile"),
        role_id=role.id if role else None,
        status="Active",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _permissions_for(db: DbSession, user: User):
    if not user.role_id:
        return []
    rows = (
        db.query(Permission)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .filter(RolePermission.role_id == user.role_id, RolePermission.is_deleted.is_(False),
                Permission.is_deleted.is_(False))
        .all()
    )
    return [PermissionOut.model_validate(p) for p in rows]


def _set_session_cookie(response: Response, token: str, max_age_seconds: int):
    response.set_cookie(
        key=SESSION_COOKIE_NAME, value=token, httponly=True,
        secure=settings.SESSION_COOKIE_SECURE, samesite="lax",
        max_age=max_age_seconds, path="/",
    )


def _finish_sso_login(db: DbSession, jwt_token: str) -> RedirectResponse:
    payload = _decode_sso_token(str(jwt_token))
    user = _sso_user_from_payload(db, payload)
    token = _create_session(db, user)
    redirect = RedirectResponse(_frontend_sso_callback(status="success"))
    _set_session_cookie(redirect, token, settings.SESSION_EXPIRE_MINUTES * 60)
    return redirect


@router.post("/login", response_model=MeOut)
def login(response: Response, form_data: OAuth2PasswordRequestForm = Depends(), db: DbSession = Depends(get_db)):
    user = db.query(User).filter(User.email == form_data.username, User.is_deleted.is_(False)).first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password")
    if user.status != "Active":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This account is inactive")

    token = _create_session(db, user)
    _set_session_cookie(response, token, settings.SESSION_EXPIRE_MINUTES * 60)
    return MeOut(**UserOut.model_validate(user).model_dump(), permissions=_permissions_for(db, user))


@router.get("/sso/status")
def sso_status():
    return {"enabled": bool(settings.SSO_ENABLED and settings.SSO_LOGIN_URL)}


@router.get("/sso/login")
def sso_login(request: Request, db: DbSession = Depends(get_db)):
    """Redirect browser to corporate SSO login.

    The identity provider should redirect back to /auth/sso/callback with a
    jwt_token query parameter. Normal email/password login remains available.
    """
    jwt_token = _sso_token_from_request(request)
    if jwt_token:
        try:
            return _finish_sso_login(db, str(jwt_token))
        except HTTPException as exc:
            return RedirectResponse(_frontend_sso_callback(status="error", message=str(exc.detail)))

    if not settings.SSO_ENABLED or not settings.SSO_LOGIN_URL:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="SSO is not configured")
    params = {"redirect_uri": settings.SSO_CALLBACK_URL or _frontend_sso_callback()}
    separator = '&' if '?' in settings.SSO_LOGIN_URL else '?'
    target = settings.SSO_LOGIN_URL + (separator + urlencode(params) if params else '')
    return RedirectResponse(target)


@router.api_route("/sso/callback", methods=["GET", "POST"])
async def sso_callback(request: Request, response: Response, db: DbSession = Depends(get_db)):
    form = {}
    if request.method == "POST":
        try:
            form = dict(await request.form())
        except Exception:
            form = {}
    jwt_token = _sso_token_from_request(request, form)
    if not jwt_token:
        return RedirectResponse(_frontend_sso_callback(status="error", message="Missing SSO token"))
    try:
        return _finish_sso_login(db, str(jwt_token))
    except HTTPException as exc:
        return RedirectResponse(_frontend_sso_callback(status="error", message=str(exc.detail)))


@router.post("/sso/exchange", response_model=MeOut)
async def sso_exchange(request: Request, response: Response, db: DbSession = Depends(get_db)):
    """Exchange a JWT received by the frontend /sso/callback route for a CostOptima session.

    Some SSO providers redirect directly to the frontend callback URL with the
    JWT in the query string or URL fragment. URL fragments never reach the
    backend during a normal redirect, so the frontend posts the token here.
    """
    payload_in = {}
    try:
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            payload_in = await request.json()
        else:
            payload_in = dict(await request.form())
    except Exception:
        payload_in = {}
    jwt_token = (
        payload_in.get("jwt_token") or payload_in.get("token") or payload_in.get("jwt")
        or payload_in.get("id_token") or payload_in.get("access_token")
    )
    if not jwt_token:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing SSO token")
    payload = _decode_sso_token(str(jwt_token))
    user = _sso_user_from_payload(db, payload)
    session_token = _create_session(db, user)
    _set_session_cookie(response, session_token, settings.SESSION_EXPIRE_MINUTES * 60)
    return MeOut(**UserOut.model_validate(user).model_dump(), permissions=_permissions_for(db, user))


@router.post("/logout")
def logout(request: Request, response: Response, db: DbSession = Depends(get_db)):
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        session = db.query(SessionModel).filter(SessionModel.id == token).first()
        if session:
            session.is_deleted = True
            db.commit()
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return {"message": "Logged out"}


@router.get("/me", response_model=MeOut)
def me(current_user: User = Depends(get_current_user), db: DbSession = Depends(get_db)):
    return MeOut(**UserOut.model_validate(current_user).model_dump(), permissions=_permissions_for(db, current_user))
