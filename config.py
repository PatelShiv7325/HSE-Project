import os
import warnings
basedir = os.path.abspath(os.path.dirname(__file__))


class Config:
    # Set SECRET_KEY as an environment variable on Render (Environment tab) --
    # any long random string, e.g.  python -c "import secrets; print(secrets.token_hex(32))"
    # Using the fallback below in production would let anyone forge login sessions.
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-secret-key-change-in-production'
    IS_PRODUCTION = bool(os.environ.get('RENDER') or os.environ.get('DATABASE_URL'))
    if not os.environ.get('SECRET_KEY') and IS_PRODUCTION:
        # Refuse to start: with the public fallback key anyone could forge an admin login cookie.
        raise RuntimeError("SECRET_KEY is not set. Add it in Render > Environment "
                           "(python -c \"import secrets; print(secrets.token_hex(32))\").")

    # Safer cookies + upload size cap
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    SESSION_COOKIE_SECURE = IS_PRODUCTION          # HTTPS only on Render, still works on http://localhost
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = 'Lax'
    REMEMBER_COOKIE_SECURE = IS_PRODUCTION
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024          # 16 MB per request (uploads, backup restore)
    _db_url = os.environ.get('DATABASE_URL') or 'sqlite:///' + os.path.join(basedir, 'hse.db')
    # Render (and some other hosts) hand out "postgres://" but SQLAlchemy 1.4+ requires "postgresql://"
    if _db_url.startswith('postgres://'):
        _db_url = _db_url.replace('postgres://', 'postgresql://', 1)
    # Use the psycopg3 driver (installed as psycopg[binary]) instead of SQLAlchemy's psycopg2 default
    if _db_url.startswith('postgresql://'):
        _db_url = _db_url.replace('postgresql://', 'postgresql+psycopg://', 1)
    SQLALCHEMY_DATABASE_URI = _db_url
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Company Information
    COMPANY_NAME = "Global HSE Associates"
    COMPANY_TAGLINE = "An ISO 9001 & 45001 Certified Company"

    # Email Configuration (optional)
    MAIL_SERVER = os.environ.get('MAIL_SERVER')
    MAIL_PORT = int(os.environ.get('MAIL_PORT') or 25)
    MAIL_USE_TLS = os.environ.get('MAIL_USE_TLS', 'true').lower() in ['true', 'on', '1']
    MAIL_USERNAME = os.environ.get('MAIL_USERNAME')
    MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD')