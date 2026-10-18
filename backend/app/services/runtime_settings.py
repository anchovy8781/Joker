"""Settings an administrator can switch in the app (one tap), stored in app_settings.

Overrides are applied onto the process-wide Settings object and re-read at most every
few seconds, so every code path that reads get_settings() sees the current value.
Only the keys listed here can be changed at runtime; everything else stays env-only.
"""

import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import AppError
from app.models import AppSetting


@dataclass(frozen=True)
class Spec:
    key: str
    label: str
    help: str
    kind: str  # "bool" | "choice"
    choices: tuple = ()


SPECS: list[Spec] = [
    Spec("bots_enabled", "가상 세력·봇 거래", "봇들이 몇 초마다 거래해 시세를 움직여요", "bool"),
    Spec("market_hours_enabled", "장 운영 시간 적용", "끄면 24시간 거래, 켜면 정해진 시간 외엔 예약 주문", "bool"),
    Spec("auto_halt_enabled", "데이터 이상 시 자동 거래정지", "끄면 데이터가 오래돼도 거래는 계속돼요", "bool"),
    Spec("listing_window_enabled", "상장 요청 시간 제한 (오후 7시~)", "끄면 언제든 상장 요청 가능", "bool"),
    Spec("listing_fee", "상장 신청 비용", "사용자 상장 요청 시 차감 (거절 시 환불)", "choice", (0, 50_000, 100_000, 300_000)),
    Spec("listing_requests_per_day", "하루 상장 요청 한도", "전체 사용자 합계", "choice", (10, 25, 50, 100)),
    Spec("fee_bps", "기본 거래 수수료", "체결 금액 대비 (bp: 10 = 0.1%)", "choice", (0, 10, 20, 50)),
    Spec("signup_grant", "가입 지급 크레딧", "새로 가입하는 사용자에게 지급", "choice", (500_000, 1_000_000, 2_000_000, 5_000_000)),
    Spec("bot_interval_seconds", "봇 거래 주기 (초)", "짧을수록 시세가 자주 움직여요 (서버 부하 증가)", "choice", (3.0, 6.0, 10.0, 20.0)),
]
BY_KEY = {s.key: s for s in SPECS}
REFRESH_SECONDS = 5.0
_defaults: dict[str, Any] = {}
_last_refresh = 0.0


def _coerce(spec: Spec, value: Any) -> Any:
    if spec.kind == "bool":
        if not isinstance(value, bool):
            raise AppError(422, "INVALID_SETTING", f"{spec.label}: true/false 값이 필요해요")
        return value
    for c in spec.choices:
        if value == c:
            return c
    raise AppError(422, "INVALID_SETTING", f"{spec.label}: 선택할 수 있는 값이 아니에요", {"choices": list(spec.choices)})


def apply(db: Session) -> None:
    """Load overrides onto the Settings object (keys without an override get their default)."""
    global _last_refresh
    s = get_settings()
    if not _defaults:
        _defaults.update({k: getattr(s, k) for k in BY_KEY})
    rows = {r.key: r.value for r in db.execute(select(AppSetting)).scalars()}
    for key, spec in BY_KEY.items():
        value = _defaults[key]
        if key in rows:
            try:
                value = _coerce(spec, rows[key])
            except AppError:
                pass  # ignore a stale/invalid stored value
        setattr(s, key, value)
    _last_refresh = time.monotonic()


def refresh(db: Session, force: bool = False) -> None:
    if force or time.monotonic() - _last_refresh >= REFRESH_SECONDS:
        apply(db)


def listing(db: Session) -> list[dict]:
    refresh(db, force=True)
    s = get_settings()
    stored = {r.key: r for r in db.execute(select(AppSetting)).scalars()}
    return [{"key": sp.key, "label": sp.label, "help": sp.help, "kind": sp.kind, "choices": list(sp.choices),
             "value": getattr(s, sp.key), "default": _defaults.get(sp.key),
             "updated_at": stored[sp.key].updated_at if sp.key in stored else None} for sp in SPECS]


def set_value(db: Session, key: str, value: Any, actor_id: int) -> Any:
    from app.services.audit import write_audit

    spec = BY_KEY.get(key)
    if spec is None:
        raise AppError(404, "UNKNOWN_SETTING", "바꿀 수 없는 설정이에요")
    value = _coerce(spec, value)
    old = getattr(get_settings(), key)
    db.execute(insert(AppSetting).values(key=key, value=value, updated_by=actor_id)
               .on_conflict_do_update(index_elements=["key"], set_={"value": value, "updated_by": actor_id}))
    write_audit(db, "settings.change", actor_id=actor_id, target_type="setting", target_id=key,
                metadata={"old": old, "new": value})
    db.flush()
    apply(db)
    return value


def reset_for_tests() -> None:
    """Restore defaults on the shared Settings object (tests)."""
    global _last_refresh
    s = get_settings()
    for k, v in _defaults.items():
        setattr(s, k, v)
    _last_refresh = 0.0
