from __future__ import annotations

import sqlite3

from decimal import Decimal

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, ConfigDict

from hlcopy import crypto, db
from hlcopy.addresses import (
    ValidationError,
    normalize_address,
    normalize_private_key,
    same_address,
    verify_key_matches,
)
from hlcopy.api.deps import SettingsDep
from hlcopy.api.errors import fail as _fail
from hlcopy.hl.errors import HlError
from hlcopy.numeric import canonical

router = APIRouter(prefix="/api/wallets", tags=["wallets"])


class WalletCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    address: str
    api_wallet_address: str
    private_key: str


class WalletUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    address: str | None = None
    api_wallet_address: str | None = None
    private_key: str | None = None


class PositionOut(BaseModel):
    coin: str
    size: str
    entry_px: str | None
    position_value: str
    unrealized_pnl: str
    leverage: int
    is_cross: bool
    liquidation_px: str | None


class OrderOut(BaseModel):
    coin: str
    oid: int
    side: str
    price: str
    size: str
    reduce_only: bool
    ts: int


class AccountOut(BaseModel):
    address: str
    account_value: str
    withdrawable: str
    margin_used: str
    positions: list[PositionOut]
    open_orders: list[OrderOut]
    fetched_at_ms: int


class WalletOut(BaseModel):
    id: int
    name: str
    address: str
    api_wallet_address: str
    key_state: str
    created_at: int
    updated_at: int


def _name(raw: str) -> str:
    name = raw.strip()
    if not name or len(name) > 64:
        _fail(400, "invalid_name", "name must be 1-64 characters")
    return name


def _out(record: db.WalletRecord) -> WalletOut:
    return WalletOut(
        id=record.id,
        name=record.name,
        address=record.address,
        api_wallet_address=record.api_wallet_address,
        key_state=record.key_state,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _normalize(payload_address: str, field: str) -> str:
    try:
        return normalize_address(payload_address, field)
    except ValidationError as exc:
        _fail(400, exc.code, exc.message)
        raise AssertionError from exc


def _private_key(raw: str) -> str:
    try:
        return normalize_private_key(raw)
    except ValidationError as exc:
        _fail(400, exc.code, exc.message)
        raise AssertionError from exc


def _check_key(key: str, api_wallet_address: str) -> None:
    try:
        verify_key_matches(key, api_wallet_address)
    except ValidationError as exc:
        _fail(400, exc.code, exc.message)


@router.get("", response_model=list[WalletOut])
def list_wallets(settings: SettingsDep) -> list[WalletOut]:
    with db.session(settings.db_path) as conn:
        return [_out(record) for record in db.list_wallets(conn)]


@router.post("", response_model=WalletOut, status_code=status.HTTP_201_CREATED)
def create_wallet(payload: WalletCreate, settings: SettingsDep) -> WalletOut:
    name = _name(payload.name)
    address = _normalize(payload.address, "address")
    api_wallet_address = _normalize(payload.api_wallet_address, "api_wallet_address")
    key = _private_key(payload.private_key)
    _check_key(key, api_wallet_address)

    secret_enc = crypto.encrypt_secret(settings.secret_enc_key, key)

    with db.session(settings.db_path) as conn:
        if db.address_taken(conn, address):
            _fail(409, "address_already_registered", "this master account is already registered")
        if db.name_taken(conn, name):
            _fail(409, "name_already_registered", "a wallet with this name already exists")
        try:
            wallet_id = db.insert_wallet(
                conn,
                name=name,
                address=address,
                api_wallet_address=api_wallet_address,
                secret_enc=secret_enc,
            )
        except sqlite3.IntegrityError as exc:
            _fail(409, "address_already_registered", "this master account is already registered")
            raise AssertionError from exc
        record = db.get_wallet(conn, wallet_id)

    assert record is not None
    return _out(record)


@router.put("/{wallet_id}", response_model=WalletOut)
def update_wallet(wallet_id: int, payload: WalletUpdate, settings: SettingsDep) -> WalletOut:
    with db.session(settings.db_path) as conn:
        record = db.get_wallet(conn, wallet_id)
        if record is None:
            _fail(404, "wallet_not_found", "wallet does not exist")

        name = _name(payload.name) if payload.name is not None else record.name
        address = (
            _normalize(payload.address, "address") if payload.address is not None else record.address
        )
        provided_api = (
            _normalize(payload.api_wallet_address, "api_wallet_address")
            if payload.api_wallet_address
            else None
        )

        api_wallet_address = record.api_wallet_address
        secret_enc = record.secret_enc
        key_state = record.key_state

        raw_key = (payload.private_key or "").strip()
        if raw_key:
            key = _private_key(raw_key)
            target = provided_api or record.api_wallet_address
            _check_key(key, target)
            api_wallet_address = target
            secret_enc = crypto.encrypt_secret(settings.secret_enc_key, key)
            key_state = "ok"
        elif provided_api is not None:
            if not same_address(provided_api, record.api_wallet_address):
                _fail(
                    400,
                    "api_wallet_requires_private_key",
                    "changing the API wallet address requires its private key",
                )
            api_wallet_address = provided_api

        if db.address_taken(conn, address, exclude_id=wallet_id):
            _fail(409, "address_already_registered", "this master account is already registered")
        if db.name_taken(conn, name, exclude_id=wallet_id):
            _fail(409, "name_already_registered", "a wallet with this name already exists")

        try:
            db.update_wallet(
                conn,
                wallet_id,
                name=name,
                address=address,
                api_wallet_address=api_wallet_address,
                secret_enc=secret_enc,
                key_state=key_state,
            )
        except sqlite3.IntegrityError as exc:
            _fail(409, "address_already_registered", "this master account is already registered")
            raise AssertionError from exc

        updated = db.get_wallet(conn, wallet_id)

    assert updated is not None
    return _out(updated)


@router.delete("/{wallet_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_wallet(wallet_id: int, settings: SettingsDep) -> None:
    with db.session(settings.db_path) as conn:
        if db.get_wallet(conn, wallet_id) is None:
            _fail(404, "wallet_not_found", "wallet does not exist")
        if db.wallet_task_count(conn, wallet_id) > 0:
            _fail(409, "wallet_in_use", "delete this wallet's tasks first")
        db.delete_wallet(conn, wallet_id)


def _dec(value: Decimal | None) -> str | None:
    return None if value is None else canonical(value)


@router.get("/{wallet_id}", response_model=WalletOut)
def get_wallet(wallet_id: int, settings: SettingsDep) -> WalletOut:
    with db.session(settings.db_path) as conn:
        record = db.get_wallet(conn, wallet_id)
    if record is None:
        _fail(404, "wallet_not_found", "wallet does not exist")
    return _out(record)


@router.get("/{wallet_id}/account", response_model=AccountOut)
def get_account(wallet_id: int, request: Request, settings: SettingsDep) -> AccountOut:
    with db.session(settings.db_path) as conn:
        record = db.get_wallet(conn, wallet_id)
    if record is None:
        _fail(404, "wallet_not_found", "wallet does not exist")

    try:
        snapshot = request.app.state.info.account(record.address)
    except HlError as exc:
        _fail(502, exc.code, exc.message)

    return AccountOut(
        address=snapshot.address,
        account_value=_dec(snapshot.account_value),
        withdrawable=_dec(snapshot.withdrawable),
        margin_used=_dec(snapshot.margin_used),
        positions=[
            PositionOut(
                coin=position.coin,
                size=_dec(position.size),
                entry_px=_dec(position.entry_px),
                position_value=_dec(position.position_value),
                unrealized_pnl=_dec(position.unrealized_pnl),
                leverage=position.leverage,
                is_cross=position.is_cross,
                liquidation_px=_dec(position.liquidation_px),
            )
            for position in snapshot.positions
        ],
        open_orders=[
            OrderOut(
                coin=order.coin,
                oid=order.oid,
                side=order.side,
                price=_dec(order.price),
                size=_dec(order.size),
                reduce_only=order.reduce_only,
                ts=order.ts,
            )
            for order in snapshot.open_orders
        ],
        fetched_at_ms=snapshot.fetched_at_ms,
    )
