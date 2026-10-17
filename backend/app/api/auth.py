from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas import LoginIn, RegisterIn, UserOut
from app.security import clear_session_cookies, optional_user, require_client_header, require_user, utcnow
from app.services import auth as auth_service

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", response_model=UserOut, status_code=201,
             dependencies=[Depends(require_client_header)])
def register(body: RegisterIn, request: Request, response: Response, db: Session = Depends(get_db)):
    user = auth_service.register(db, request, response, body.username, body.password, body.captcha_token)
    db.commit()
    return user


@router.post("/login", response_model=UserOut, dependencies=[Depends(require_client_header)])
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    user = auth_service.login(db, request, response, body.username, body.password)
    db.commit()
    return user


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, user=Depends(require_user), db: Session = Depends(get_db)):
    session = request.state.session
    session.revoked_at = utcnow()
    db.merge(session)
    db.commit()
    clear_session_cookies(response)
    response.status_code = 204
    return response


@router.get("/me", response_model=UserOut | None)
def me(user=Depends(optional_user)):
    return user
