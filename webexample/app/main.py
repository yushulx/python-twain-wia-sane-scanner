import base64
import binascii
import hashlib
import io
import os
import secrets
import sqlite3
import sys
import threading
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import jwt
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import HTMLResponse, Response
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel, Field


def load_env_file(env_path: Path) -> None:
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue

        key, value = line.split('=', 1)
        key = key.strip()
        value = value.strip()
        if not key or key in os.environ:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        os.environ[key] = value


WEBEXAMPLE_ROOT = Path(__file__).resolve().parent.parent
load_env_file(WEBEXAMPLE_ROOT / '.env')

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dynamsoftservice import JobStatus, ScannerController, ScannerServiceError, ScannerType


def get_int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    return int(value, 0)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def utcnow_string() -> str:
    return utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')


APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / 'static'
DATABASE_PATH = Path(os.getenv('REMOTE_SCAN_DB_PATH', str(APP_DIR / 'remote_scan.db')))
SERVICE_HOST = os.getenv('DWT_SERVICE_HOST', 'http://127.0.0.1:18622')
LICENSE_KEY = os.getenv('DWT_LICENSE_KEY', 'DLS2eyJoYW5kc2hha2VDb2RlIjoiMjAwMDAxLTE2NDk4Mjk3OTI2MzUiLCJvcmdhbml6YXRpb25JRCI6IjIwMDAwMSIsInNlc3Npb25QYXNzd29yZCI6IndTcGR6Vm05WDJrcEQ5YUoifQ==')
JWT_SECRET = os.getenv('REMOTE_SCAN_JWT_SECRET', 'change-me-before-production')
BOOTSTRAP_ADMIN_USERNAME = os.getenv('REMOTE_SCAN_ADMIN_USERNAME', '').strip().lower()
BOOTSTRAP_ADMIN_PASSWORD = os.getenv('REMOTE_SCAN_ADMIN_PASSWORD', '')
BOOTSTRAP_ADMIN_FULL_NAME = (os.getenv('REMOTE_SCAN_ADMIN_FULL_NAME', 'Gateway Admin') or 'Gateway Admin').strip()
JWT_ALGORITHM = 'HS256'
ACCESS_TOKEN_TTL_MINUTES = get_int_env('ACCESS_TOKEN_TTL_MINUTES', 120)
SCANNER_LOCK_TTL_SECONDS = get_int_env('SCANNER_LOCK_TTL_SECONDS', 600)
DEFAULT_SCANNER_TYPES = ScannerType.TWAINSCANNER | ScannerType.TWAINX64SCANNER
SCANNER_TYPE_MASK = get_int_env('REMOTE_SCAN_SCANNER_TYPES', DEFAULT_SCANNER_TYPES)
SERVICE_VERIFY = os.getenv('DWT_SERVICE_VERIFY', 'true').lower() == 'true'

DB_LOCK = threading.Lock()
scanner_controller = ScannerController(timeout=120, verify=SERVICE_VERIFY)
oauth2_scheme = OAuth2PasswordBearer(tokenUrl='/api/auth/token')

app = FastAPI(
    title='Secure Remote Scanning Gateway',
    docs_url='/api/docs',
    openapi_url='/api/openapi.json',
)
app.mount('/static', StaticFiles(directory=str(STATIC_DIR)), name='static')


class RegistrationRequest(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    full_name: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=8, max_length=128)


class UserResponse(BaseModel):
    username: str
    full_name: str
    created_at: str
    is_admin: bool = False


class DeleteUserResponse(BaseModel):
    username: str


class AdminToggleRequest(BaseModel):
    is_admin: bool


class PublicSetupResponse(BaseModel):
    admin_usernames: List[str] = Field(default_factory=list)
    configured_admin_username: str = ''


class ExportRequest(BaseModel):
    images: List[str] = Field(default_factory=list)
    image_type: str = 'image/png'
    file_stem: str = Field(default='scan-output', min_length=1, max_length=80)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = 'bearer'
    expires_in: int


class ScanRequest(BaseModel):
    resolution: int = Field(default=200, ge=75, le=600)
    pixel_type: int = Field(default=2, ge=0, le=2)
    feeder_enabled: bool = True
    duplex_enabled: bool = False
    show_ui: bool = False
    image_type: str = 'image/png'
    job_timeout: int = Field(default=300, ge=60, le=1800)
    scanner_failure_timeout: int = Field(default=120, ge=15, le=1800)


def get_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(str(DATABASE_PATH), check_same_thread=False)
    connection.row_factory = sqlite3.Row
    return connection


def validate_bootstrap_admin_config() -> None:
    if not BOOTSTRAP_ADMIN_USERNAME and not BOOTSTRAP_ADMIN_PASSWORD:
        return
    if not BOOTSTRAP_ADMIN_USERNAME or not BOOTSTRAP_ADMIN_PASSWORD:
        raise RuntimeError('Set both REMOTE_SCAN_ADMIN_USERNAME and REMOTE_SCAN_ADMIN_PASSWORD, or leave both unset.')
    if not is_valid_username(BOOTSTRAP_ADMIN_USERNAME):
        raise RuntimeError('REMOTE_SCAN_ADMIN_USERNAME may only include letters, numbers, dots, dashes, and underscores.')
    if len(BOOTSTRAP_ADMIN_PASSWORD) < 8:
        raise RuntimeError('REMOTE_SCAN_ADMIN_PASSWORD must be at least 8 characters long.')


def init_database() -> None:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_connection() as connection:
        connection.execute('PRAGMA journal_mode=WAL')
        connection.execute(
            '''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                full_name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            '''
        )
        user_columns = {
            row['name']
            for row in connection.execute('PRAGMA table_info(users)').fetchall()
        }
        if 'is_admin' not in user_columns:
            connection.execute('ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0')
        connection.execute(
            '''
            UPDATE users
            SET is_admin = 1
            WHERE id = (
                SELECT id
                FROM users
                ORDER BY created_at ASC, id ASC
                LIMIT 1
            )
            AND NOT EXISTS (
                SELECT 1
                FROM users
                WHERE is_admin = 1
            )
            '''
        )
        connection.execute(
            '''
            CREATE TABLE IF NOT EXISTS scanner_locks (
                scanner_id TEXT PRIMARY KEY,
                scanner_name TEXT NOT NULL,
                owner_username TEXT NOT NULL,
                lock_token TEXT NOT NULL,
                status TEXT NOT NULL,
                job_uid TEXT,
                acquired_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
            '''
        )
        connection.execute(
            '''
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                action TEXT NOT NULL,
                scanner_id TEXT,
                detail TEXT,
                created_at TEXT NOT NULL
            )
            '''
        )


def ensure_bootstrap_admin_account() -> None:
    if not BOOTSTRAP_ADMIN_USERNAME or not BOOTSTRAP_ADMIN_PASSWORD:
        return

    with DB_LOCK:
        with get_connection() as connection:
            existing_user = connection.execute(
                'SELECT username, created_at FROM users WHERE username = ?',
                (BOOTSTRAP_ADMIN_USERNAME,),
            ).fetchone()
            if existing_user:
                connection.execute(
                    'UPDATE users SET full_name = ?, password_hash = ?, is_admin = 1 WHERE username = ?',
                    (BOOTSTRAP_ADMIN_FULL_NAME, hash_password(BOOTSTRAP_ADMIN_PASSWORD), BOOTSTRAP_ADMIN_USERNAME),
                )
                return

            connection.execute(
                'INSERT INTO users (username, full_name, password_hash, created_at, is_admin) VALUES (?, ?, ?, ?, ?)',
                (
                    BOOTSTRAP_ADMIN_USERNAME,
                    BOOTSTRAP_ADMIN_FULL_NAME,
                    hash_password(BOOTSTRAP_ADMIN_PASSWORD),
                    utcnow_string(),
                    1,
                ),
            )


def write_audit_log(username: str, action: str, scanner_id: str = '', detail: str = '') -> None:
    with DB_LOCK:
        with get_connection() as connection:
            connection.execute(
                'INSERT INTO audit_log (username, action, scanner_id, detail, created_at) VALUES (?, ?, ?, ?, ?)',
                (username, action, scanner_id, detail, utcnow_string()),
            )


def is_valid_username(username: str) -> bool:
    allowed = set('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.')
    return bool(username) and all(character in allowed for character in username)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 390000)
    return '{salt}:{digest}'.format(salt=salt.hex(), digest=digest.hex())


def verify_password(password: str, stored_value: str) -> bool:
    salt_hex, digest_hex = stored_value.split(':', 1)
    candidate = hashlib.pbkdf2_hmac(
        'sha256',
        password.encode('utf-8'),
        bytes.fromhex(salt_hex),
        390000,
    )
    return secrets.compare_digest(candidate.hex(), digest_hex)


def serialize_user_record(user_record: sqlite3.Row) -> Dict[str, Any]:
    return {
        'username': user_record['username'],
        'full_name': user_record['full_name'],
        'created_at': user_record['created_at'],
        'is_admin': bool(user_record['is_admin']),
    }


def get_user_record(username: str) -> Optional[sqlite3.Row]:
    normalized_username = username.strip().lower()
    with get_connection() as connection:
        return connection.execute(
            'SELECT username, full_name, password_hash, created_at, is_admin FROM users WHERE username = ?',
            (normalized_username,),
        ).fetchone()


def create_user_account(payload: RegistrationRequest) -> Dict[str, Any]:
    username = payload.username.strip().lower()
    full_name = payload.full_name.strip()
    if not is_valid_username(username):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Usernames may only include letters, numbers, dots, dashes, and underscores.',
        )

    with DB_LOCK:
        with get_connection() as connection:
            existing_user = connection.execute(
                'SELECT username FROM users WHERE username = ?',
                (username,),
            ).fetchone()
            if existing_user:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail='That username is already registered.',
                )

            created_at = utcnow_string()
            admin_exists = connection.execute(
                'SELECT 1 FROM users WHERE is_admin = 1 LIMIT 1'
            ).fetchone()
            is_admin = 0 if admin_exists else 1
            connection.execute(
                'INSERT INTO users (username, full_name, password_hash, created_at, is_admin) VALUES (?, ?, ?, ?, ?)',
                (username, full_name, hash_password(payload.password), created_at, is_admin),
            )

    write_audit_log(username, 'auth.registered')
    return {
        'username': username,
        'full_name': full_name,
        'created_at': created_at,
        'is_admin': bool(is_admin),
    }


def authenticate_user(username: str, password: str) -> Optional[Dict[str, Any]]:
    user_record = get_user_record(username)
    if not user_record:
        return None
    if not verify_password(password, user_record['password_hash']):
        return None
    return serialize_user_record(user_record)


def create_access_token(username: str) -> TokenResponse:
    expires_at = utcnow() + timedelta(minutes=ACCESS_TOKEN_TTL_MINUTES)
    access_token = jwt.encode({'sub': username, 'exp': expires_at}, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return TokenResponse(
        access_token=access_token,
        expires_in=ACCESS_TOKEN_TTL_MINUTES * 60,
    )


def get_current_user(token: str = Depends(oauth2_scheme)) -> Dict[str, Any]:
    authentication_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail='Could not validate credentials.',
        headers={'WWW-Authenticate': 'Bearer'},
    )
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.InvalidTokenError:
        raise authentication_error

    username = payload.get('sub')
    if not username:
        raise authentication_error

    user_record = get_user_record(username)
    if not user_record:
        raise authentication_error

    return serialize_user_record(user_record)


def require_admin_user(current_user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    if not current_user['is_admin']:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='Only administrators can manage registered users.',
        )
    return current_user


def list_user_accounts() -> List[Dict[str, Any]]:
    with get_connection() as connection:
        rows = connection.execute(
            'SELECT username, full_name, created_at, is_admin FROM users ORDER BY created_at ASC, username ASC'
        ).fetchall()
    return [serialize_user_record(row) for row in rows]


def list_admin_usernames() -> List[str]:
    with get_connection() as connection:
        rows = connection.execute(
            'SELECT username FROM users WHERE is_admin = 1 ORDER BY username ASC'
        ).fetchall()
    return [row['username'] for row in rows]


def delete_user_account(username: str, current_user: Dict[str, Any]) -> Dict[str, Any]:
    normalized_username = username.strip().lower()
    if not normalized_username:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Username is required.')
    if normalized_username == current_user['username']:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Administrators cannot delete the account they are currently using.',
        )

    with DB_LOCK:
        with get_connection() as connection:
            target = connection.execute(
                'SELECT username, full_name, created_at, is_admin FROM users WHERE username = ?',
                (normalized_username,),
            ).fetchone()
            if not target:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='User not found.')

            admin_count = connection.execute(
                'SELECT COUNT(*) AS count FROM users WHERE is_admin = 1'
            ).fetchone()['count']
            if target['is_admin'] and admin_count <= 1:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail='The last administrator account cannot be deleted.',
                )

            connection.execute('DELETE FROM scanner_locks WHERE owner_username = ?', (normalized_username,))
            connection.execute('DELETE FROM users WHERE username = ?', (normalized_username,))

    write_audit_log(current_user['username'], 'auth.user_deleted', detail=normalized_username)
    write_audit_log(normalized_username, 'auth.deleted', detail='Deleted by {username}'.format(username=current_user['username']))
    return {'username': normalized_username}


def toggle_user_admin(username: str, make_admin: bool, current_user: Dict[str, Any]) -> Dict[str, Any]:
    normalized_username = username.strip().lower()
    if not normalized_username:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Username is required.')
    if normalized_username == current_user['username']:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='You cannot change your own administrator status.',
        )
    with DB_LOCK:
        with get_connection() as connection:
            target = connection.execute(
                'SELECT username, full_name, created_at, is_admin FROM users WHERE username = ?',
                (normalized_username,),
            ).fetchone()
            if not target:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='User not found.')
            if not make_admin and target['is_admin']:
                admin_count = connection.execute(
                    'SELECT COUNT(*) AS count FROM users WHERE is_admin = 1'
                ).fetchone()['count']
                if admin_count <= 1:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail='Cannot remove administrator status from the last administrator.',
                    )
            connection.execute(
                'UPDATE users SET is_admin = ? WHERE username = ?',
                (1 if make_admin else 0, normalized_username),
            )
            updated = connection.execute(
                'SELECT username, full_name, created_at, is_admin FROM users WHERE username = ?',
                (normalized_username,),
            ).fetchone()
    action = 'granted' if make_admin else 'revoked'
    write_audit_log(
        current_user['username'],
        'auth.admin_toggle',
        detail='Admin access {action} for {target}'.format(action=action, target=normalized_username),
    )
    return serialize_user_record(updated)


def slugify_file_stem(file_stem: str) -> str:
    filtered = []
    for character in file_stem.strip():
        if character.isalnum() or character in ('-', '_'):
            filtered.append(character)
        elif character.isspace():
            filtered.append('-')
    cleaned = ''.join(filtered).strip('-_')
    return cleaned or 'scan-output'


def decode_export_images(payload: ExportRequest) -> List[Image.Image]:
    if payload.image_type not in ('image/png', 'image/jpeg'):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='image_type must be image/png or image/jpeg.')
    if not payload.images:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='At least one scanned page is required.')

    decoded_images: List[Image.Image] = []
    for index, image_payload in enumerate(payload.images, start=1):
        try:
            image_bytes = base64.b64decode(image_payload, validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail='Invalid base64 image payload for page {index}.'.format(index=index),
            )

        try:
            with Image.open(io.BytesIO(image_bytes)) as image:
                image.load()
                decoded_images.append(image.copy())
        except OSError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail='Unsupported image payload for page {index}.'.format(index=index),
            )

    return decoded_images


def image_to_png_bytes(image: Image.Image) -> bytes:
    output = io.BytesIO()
    if image.mode in ('RGBA', 'LA'):
        prepared_image = image
    elif image.mode in ('RGB', 'L'):
        prepared_image = image
    else:
        prepared_image = image.convert('RGBA')
    prepared_image.save(output, format='PNG')
    return output.getvalue()


def image_to_pdf_page(image: Image.Image) -> Image.Image:
    if image.mode in ('RGBA', 'LA'):
        alpha = image.getchannel('A')
        flattened = Image.new('RGB', image.size, 'white')
        flattened.paste(image.convert('RGBA'), mask=alpha)
        return flattened
    if image.mode != 'RGB':
        return image.convert('RGB')
    return image


def build_pdf_bytes(images: List[Image.Image]) -> bytes:
    pdf_pages = [image_to_pdf_page(image) for image in images]
    output = io.BytesIO()
    first_page, *remaining_pages = pdf_pages
    first_page.save(output, format='PDF', save_all=True, append_images=remaining_pages)
    return output.getvalue()


def prune_expired_locks(connection: sqlite3.Connection) -> None:
    connection.execute('DELETE FROM scanner_locks WHERE expires_at <= ?', (utcnow_string(),))


def get_active_locks() -> Dict[str, Dict[str, Any]]:
    with DB_LOCK:
        with get_connection() as connection:
            prune_expired_locks(connection)
            rows = connection.execute(
                'SELECT scanner_id, scanner_name, owner_username, status, job_uid, acquired_at, expires_at FROM scanner_locks'
            ).fetchall()

    locks: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        locks[row['scanner_id']] = {
            'scanner_id': row['scanner_id'],
            'scanner_name': row['scanner_name'],
            'owner_username': row['owner_username'],
            'status': row['status'],
            'job_uid': row['job_uid'],
            'acquired_at': row['acquired_at'],
            'expires_at': row['expires_at'],
        }
    return locks


def acquire_lock(scanner_id: str, scanner_name: str, username: str) -> Optional[Dict[str, Any]]:
    with DB_LOCK:
        with get_connection() as connection:
            prune_expired_locks(connection)
            existing_lock = connection.execute(
                'SELECT scanner_id, scanner_name, owner_username, status, job_uid, acquired_at, expires_at FROM scanner_locks WHERE scanner_id = ?',
                (scanner_id,),
            ).fetchone()
            if existing_lock and existing_lock['owner_username'] != username:
                return {
                    'scanner_id': existing_lock['scanner_id'],
                    'scanner_name': existing_lock['scanner_name'],
                    'owner_username': existing_lock['owner_username'],
                    'status': existing_lock['status'],
                    'job_uid': existing_lock['job_uid'],
                    'acquired_at': existing_lock['acquired_at'],
                    'expires_at': existing_lock['expires_at'],
                }

            now = utcnow_string()
            expires_at = (utcnow() + timedelta(seconds=SCANNER_LOCK_TTL_SECONDS)).strftime('%Y-%m-%dT%H:%M:%SZ')
            connection.execute(
                '''
                INSERT INTO scanner_locks (
                    scanner_id, scanner_name, owner_username, lock_token, status, job_uid, acquired_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(scanner_id) DO UPDATE SET
                    scanner_name = excluded.scanner_name,
                    owner_username = excluded.owner_username,
                    lock_token = excluded.lock_token,
                    status = excluded.status,
                    job_uid = excluded.job_uid,
                    acquired_at = excluded.acquired_at,
                    expires_at = excluded.expires_at
                ''',
                (scanner_id, scanner_name, username, uuid.uuid4().hex, 'pending', '', now, expires_at),
            )
    return None


def update_lock(scanner_id: str, username: str, status_value: str, job_uid: str = '') -> None:
    expires_at = (utcnow() + timedelta(seconds=SCANNER_LOCK_TTL_SECONDS)).strftime('%Y-%m-%dT%H:%M:%SZ')
    with DB_LOCK:
        with get_connection() as connection:
            connection.execute(
                'UPDATE scanner_locks SET status = ?, job_uid = ?, expires_at = ? WHERE scanner_id = ? AND owner_username = ?',
                (status_value, job_uid, expires_at, scanner_id, username),
            )


def release_lock(scanner_id: str, username: str) -> None:
    with DB_LOCK:
        with get_connection() as connection:
            connection.execute(
                'DELETE FROM scanner_locks WHERE scanner_id = ? AND owner_username = ?',
                (scanner_id, username),
            )


def get_scanner_id(scanner: Dict[str, Any]) -> str:
    return hashlib.sha256(scanner['device'].encode('utf-8')).hexdigest()[:16]


def list_scanners() -> List[Dict[str, Any]]:
    scanners = scanner_controller.getDevices(SERVICE_HOST, SCANNER_TYPE_MASK)
    locks = get_active_locks()
    items: List[Dict[str, Any]] = []
    for scanner in scanners:
        scanner_id = get_scanner_id(scanner)
        active_lock = locks.get(scanner_id)
        items.append(
            {
                'id': scanner_id,
                'name': scanner.get('name', 'Unknown scanner'),
                'type': scanner.get('type'),
                'locked': bool(active_lock),
                'locked_by': active_lock['owner_username'] if active_lock else '',
                'lock_status': active_lock['status'] if active_lock else 'idle',
                'lock_expires_at': active_lock['expires_at'] if active_lock else '',
            }
        )
    return items


def find_scanner(scanner_id: str) -> Optional[Dict[str, Any]]:
    scanners = scanner_controller.getDevices(SERVICE_HOST, SCANNER_TYPE_MASK)
    for scanner in scanners:
        if get_scanner_id(scanner) == scanner_id:
            return scanner
    return None


@app.middleware('http')
async def add_security_headers(request, call_next):
    response = await call_next(request)
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'same-origin'
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "img-src 'self' data:; "
        "style-src 'self'; "
        "script-src 'self'; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    )
    return response


@app.on_event('startup')
def on_startup() -> None:
    validate_bootstrap_admin_config()
    init_database()
    ensure_bootstrap_admin_account()


@app.on_event('shutdown')
def on_shutdown() -> None:
    scanner_controller.close()


@app.get('/', response_class=HTMLResponse)
def index() -> str:
    return (STATIC_DIR / 'index.html').read_text(encoding='utf-8')


@app.post('/api/auth/register', response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegistrationRequest) -> Dict[str, Any]:
    return create_user_account(payload)


@app.get('/api/public/setup', response_model=PublicSetupResponse)
def public_setup() -> Dict[str, Any]:
    return {
        'admin_usernames': list_admin_usernames(),
        'configured_admin_username': BOOTSTRAP_ADMIN_USERNAME,
    }


@app.post('/api/auth/token', response_model=TokenResponse)
def login(form_data: OAuth2PasswordRequestForm = Depends()) -> TokenResponse:
    user = authenticate_user(form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Incorrect username or password.',
            headers={'WWW-Authenticate': 'Bearer'},
        )
    write_audit_log(user['username'], 'auth.logged_in')
    return create_access_token(user['username'])


@app.get('/api/auth/me', response_model=UserResponse)
def me(current_user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    return current_user


@app.get('/api/users', response_model=List[UserResponse])
def users(current_user: Dict[str, Any] = Depends(require_admin_user)) -> List[Dict[str, Any]]:
    del current_user
    return list_user_accounts()


@app.delete('/api/users/{username}', response_model=DeleteUserResponse)
def delete_user(username: str, current_user: Dict[str, Any] = Depends(require_admin_user)) -> Dict[str, Any]:
    return delete_user_account(username, current_user)


@app.patch('/api/users/{username}/admin', response_model=UserResponse)
def patch_user_admin(username: str, payload: AdminToggleRequest, current_user: Dict[str, Any] = Depends(require_admin_user)) -> Dict[str, Any]:
    return toggle_user_admin(username, payload.is_admin, current_user)


@app.get('/api/scanners')
def scanners(current_user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    del current_user
    server_info = scanner_controller.getServerInfo(SERVICE_HOST)
    return {
        'service_host': SERVICE_HOST,
        'server': server_info,
        'scanners': list_scanners(),
    }


@app.post('/api/scanners/{scanner_id}/scan')
def scan(
    scanner_id: str,
    payload: ScanRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    if payload.image_type not in ('image/png', 'image/jpeg'):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='image_type must be image/png or image/jpeg.')

    if not LICENSE_KEY:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail='DWT_LICENSE_KEY is not configured on the server.',
        )

    scanner = find_scanner(scanner_id)
    if not scanner:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Scanner not found.')

    conflicting_lock = acquire_lock(scanner_id, scanner.get('name', 'Unknown scanner'), current_user['username'])
    if conflicting_lock:
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail={
                'message': 'Scanner is currently locked by another user.',
                'locked_by': conflicting_lock['owner_username'],
                'status': conflicting_lock['status'],
                'expires_at': conflicting_lock['expires_at'],
            },
        )

    job_uid = ''
    try:
        write_audit_log(current_user['username'], 'scan.locked', scanner_id=scanner_id, detail=scanner['name'])
        job = scanner_controller.createJob(
            SERVICE_HOST,
            {
                'license': LICENSE_KEY,
                'device': scanner['device'],
                'autoRun': False,
                'jobTimeout': payload.job_timeout,
                'scannerFailureTimeout': payload.scanner_failure_timeout,
                'requestFocusForScanningUI': False,
                'checkFeederLoaded': payload.feeder_enabled,
                'config': {
                    'IfShowUI': payload.show_ui,
                    'PixelType': payload.pixel_type,
                    'Resolution': payload.resolution,
                    'IfFeederEnabled': payload.feeder_enabled,
                    'IfDuplexEnabled': payload.duplex_enabled,
                    'IfCloseSourceAfterAcquire': True,
                },
            },
        )
        job_uid = job.get('jobuid', '')
        if not job_uid:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=job or scanner_controller.last_error or {'message': 'Failed to create scan job.'},
            )

        update_lock(scanner_id, current_user['username'], 'pending', job_uid=job_uid)

        start_result = scanner_controller.updateJob(SERVICE_HOST, job_uid, {'status': JobStatus.RUNNING})
        if start_result.get('status') not in (JobStatus.RUNNING, JobStatus.COMPLETED):
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=start_result or scanner_controller.last_error or {'message': 'Failed to start scan job.'},
            )

        update_lock(scanner_id, current_user['username'], 'scanning', job_uid=job_uid)
        images = scanner_controller.getImageStreams(SERVICE_HOST, job_uid, imageType=payload.image_type)
        job_info = scanner_controller.checkJob(SERVICE_HOST, job_uid)
        if job_info.get('status') == JobStatus.FAULTED:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=job_info)

        encoded_images = [base64.b64encode(image).decode('utf-8') for image in images]
        write_audit_log(
            current_user['username'],
            'scan.completed',
            scanner_id=scanner_id,
            detail='{count} page(s)'.format(count=len(encoded_images)),
        )
        return {
            'scanner': {'id': scanner_id, 'name': scanner['name']},
            'page_count': len(encoded_images),
            'image_type': payload.image_type,
            'job_status': job_info.get('status', JobStatus.COMPLETED),
            'images': encoded_images,
        }
    except ScannerServiceError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=error.details or {'message': str(error)},
        )
    finally:
        if job_uid:
            scanner_controller.deleteJob(SERVICE_HOST, job_uid)
        release_lock(scanner_id, current_user['username'])


@app.post('/api/exports/pdf')
def export_pdf(
    payload: ExportRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Response:
    pdf_bytes = build_pdf_bytes(decode_export_images(payload))
    filename = '{stem}.pdf'.format(stem=slugify_file_stem(payload.file_stem))
    write_audit_log(current_user['username'], 'export.pdf', detail='{count} page(s)'.format(count=len(payload.images)))
    return Response(
        content=pdf_bytes,
        media_type='application/pdf',
        headers={'Content-Disposition': 'attachment; filename="{name}"'.format(name=filename)},
    )


@app.post('/api/exports/png')
def export_png(
    payload: ExportRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Response:
    images = decode_export_images(payload)
    file_stem = slugify_file_stem(payload.file_stem)
    write_audit_log(current_user['username'], 'export.png', detail='{count} page(s)'.format(count=len(images)))

    if len(images) == 1:
        return Response(
            content=image_to_png_bytes(images[0]),
            media_type='image/png',
            headers={'Content-Disposition': 'attachment; filename="{name}"'.format(name='{stem}.png'.format(stem=file_stem))},
        )

    archive = io.BytesIO()
    with zipfile.ZipFile(archive, mode='w', compression=zipfile.ZIP_DEFLATED) as zip_handle:
        for index, image in enumerate(images, start=1):
            zip_handle.writestr(
                '{stem}-{index:02d}.png'.format(stem=file_stem, index=index),
                image_to_png_bytes(image),
            )
    return Response(
        content=archive.getvalue(),
        media_type='application/zip',
        headers={'Content-Disposition': 'attachment; filename="{name}"'.format(name='{stem}-png.zip'.format(stem=file_stem))},
    )