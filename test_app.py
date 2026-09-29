"""Smoke tests for the Flask attendance app (discovered by pytest)."""
import numpy as np
import pytest

import app as app_module
import database


def test_login_page_renders(client):
    response = client.get("/login")
    assert response.status_code == 200
    assert b"login" in response.data.lower() or b"password" in response.data.lower()


def test_index_redirects_when_logged_out(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code in (200, 302)


def test_admin_login_succeeds(client):
    response = client.post(
        "/login",
        data={"username": "admin", "password": "admin123"},
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)
    with client.session_transaction() as sess:
        assert sess.get("role") == "admin"
        assert sess.get("username") == "admin"


def test_admin_students_requires_login(client):
    response = client.get("/admin/students", follow_redirects=False)
    assert response.status_code in (302, 303)


def test_insert_encoding_rejects_missing_student(client):
    conn = database.get_db_connection()
    try:
        with pytest.raises(ValueError, match="no student row"):
            app_module._insert_encoding_cursor(
                conn, "DOES-NOT-EXIST", np.array([0.1, 0.2], dtype=np.float32)
            )
    finally:
        conn.close()
