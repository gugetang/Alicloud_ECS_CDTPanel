from app.security import (decrypt_secret, encrypt_secret, hash_password,
                          verify_password)


def test_password_roundtrip():
    h = hash_password("correct-horse")
    assert verify_password("correct-horse", h)
    assert not verify_password("wrong", h)
    assert not verify_password("correct-horse", "garbage")


def test_secret_encrypt_roundtrip():
    enc = encrypt_secret("LTAI5t-secret-xyz")
    assert enc != "LTAI5t-secret-xyz"
    assert decrypt_secret(enc) == "LTAI5t-secret-xyz"
    assert decrypt_secret("") == ""
