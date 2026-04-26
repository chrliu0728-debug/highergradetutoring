"""Gunicorn entry point.

Run on the production VM as:
    gunicorn -w 2 -b 127.0.0.1:5000 wsgi:app
"""
from app import app  # noqa: F401
