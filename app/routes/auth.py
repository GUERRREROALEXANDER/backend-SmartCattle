from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.config import Settings
from app.core.security import create_access_token
from app.dependencies import (
    Clock,
    CurrentUser,
    get_app_settings,
    get_clock,
    get_login_throttle,
    get_user_store,
    require_jwt_secret,
)
from app.schemas.auth import LoginRequest, TokenResponse, User, UserCreate, UserRole
from app.services.login_throttle import LoginThrottle
from app.services.user_store import EmailAlreadyRegisteredError, StoredUser, UserStore

router = APIRouter(prefix="/api/auth", tags=["auth"])

#: Returned for a wrong address and for a wrong password alike. Telling them
#: apart turns the login endpoint into a way to find out which addresses have
#: accounts.
INVALID_CREDENTIALS = "Invalid email or password"


def _token(user: StoredUser, settings: Settings, secret: str, clock: Clock) -> TokenResponse:
    expires_in = timedelta(minutes=settings.access_token_minutes)
    return TokenResponse(
        access_token=create_access_token(
            subject=str(user.id), secret=secret, expires_in=expires_in,
            role=user.role.value, now=clock(),
        ),
        expires_in=int(expires_in.total_seconds()),
        user=User(**user.model_dump(exclude={"password_hash"})),
    )


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(
    data: UserCreate,
    store: UserStore = Depends(get_user_store),
    settings: Settings = Depends(get_app_settings),
    secret: str = Depends(require_jwt_secret),
    clock: Clock = Depends(get_clock),
) -> TokenResponse:
    """Creates an account and signs it in.

    The first account registered becomes the owner, because somebody has to be
    and there is no other channel to grant it. Every account after it is a
    worker; promoting one is not an API operation yet.
    """
    role = UserRole.OWNER if store.count() == 0 else UserRole.WORKER
    try:
        user = store.create(data, role, clock())
    except EmailAlreadyRegisteredError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That email is already registered",
        ) from error
    return _token(user, settings, secret, clock)


@router.post("/login", response_model=TokenResponse)
def login(
    data: LoginRequest,
    store: UserStore = Depends(get_user_store),
    settings: Settings = Depends(get_app_settings),
    secret: str = Depends(require_jwt_secret),
    throttle: LoginThrottle = Depends(get_login_throttle),
    clock: Clock = Depends(get_clock),
) -> TokenResponse:
    now = clock()
    retry_after = throttle.locked_for(data.email, now)
    if retry_after is not None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed attempts. Try again later.",
            headers={"Retry-After": str(retry_after)},
        )

    user = store.by_email(data.email)
    # verify_password runs even when there is no such user, against a throwaway
    # hash, so a missing address and a wrong password take the same time.
    # Returning early here would make the response time itself reveal which
    # addresses have accounts.
    correct = throttle.verify(data.password, user.password_hash if user else None)

    if user is None or not correct or not user.is_active:
        throttle.record_failure(data.email, now)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=INVALID_CREDENTIALS,
            headers={"WWW-Authenticate": "Bearer"},
        )

    throttle.clear(data.email)
    store.record_login(user.id, now)
    return _token(user.model_copy(update={"last_login_at": now}), settings, secret, clock)


@router.get("/me", response_model=User)
def me(user: CurrentUser) -> User:
    return User(**user.model_dump(exclude={"password_hash"}))
